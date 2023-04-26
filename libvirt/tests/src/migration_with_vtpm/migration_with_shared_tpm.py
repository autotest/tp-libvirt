import os

from avocado.utils import distro
from avocado.utils import process

from virttest import ceph
from virttest import remote
from virttest import utils_package
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_secret
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps


def check_tpm_security_context(params, vm, test, expected_contexts, remote=False):
    """
    Check TPM state file security contexts

    :param params: dict, test parameters
    :param vm: VM object
    :param test: test object
    :param expected_contexts: expected tpm security context
    :param remote: True to check context on remote
    """
    test.log.debug("Check tpm security context: %s (remote)", remote)
    domuuid = vm.get_uuid()
    cmd = "ls -hZR /var/lib/libvirt/swtpm/%s/tpm2/tpm2-00.permall" % domuuid
    if remote:
        cmd_result = remote.run_remote_cmd(cmd, params)
    else:
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
    if cmd_result.exit_status:
        test.error("Fail to run '%s'." % cmd)
    if expected_contexts not in cmd_result.stdout_text:
        test.fail("Fail to find '%s' in '%s'." % (expected_contexts, cmd_result.stdout))


def check_swtpm_process(params, test):
    """
    Check swtpm process

    :param params: dict, test parameters
    :param test: test object
    """
    test.log.debug("Check swtpm process.")
    cmd = "ps aux|grep swtpm"
    cmd_result = process.run(cmd, ignore_status=True, shell=True)
    if cmd_result.exit_status:
        test.error("Fail to run '%s'." % cmd)
    check_str = "--migration release-lock-outgoing"
    if check_str not in cmd_result.stdout_text:
        test.fail("Fail to check swtpm process.")


def check_vtpm_func(params, vm, test, remote=False):
    """
    Check vtpm function

    :param params: dict, test parameters
    :param vm: VM object
    :param test: test object
    :param remote: True to check context on remote
    """
    tpm_cmd = params.get("tpm_cmd")
    dest_uri = params.get("virsh_migrate_desturi")
    src_uri = params.get("virsh_migrate_connect_uri")
    test.log.debug("Check vtpm func: %s (remote).", remote)
    if remote:
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.connect_uri = dest_uri
    if vm.serial_console is None:
        vm.create_serial_console()
    vm_session = vm.wait_for_serial_login(timeout=240)
    if not utils_package.package_install("tpm2-tools", vm_session):
        test.error("Failed to install tpm2-tools in vm")
    cmd_result = vm_session.cmd_status(tpm_cmd)
    vm_session.close()
    if remote:
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.connect_uri = src_uri
    if cmd_result:
        test.fail("Fail to run '%s': %s." % (tpm_cmd, cmd_result))


def set_secret(params):
    """
    Set secret

    :param params: dict, test parameters
    """
    auth_sec_dict = eval(params.get("auth_sec_dict"))
    secret_value = params.get("secret_value")
    remote_pwd = params.get("migrate_dest_pwd")
    remote_ip = params.get("migrate_dest_host")
    remote_user = params.get("remote_user", "root")

    def _create_secret(session=None, remote_args=None):
        """
        create secret

        :param session: a session object of remote host
        :param remote_args: remote host parameters
        """
        libvirt_secret.clean_up_secrets(session)
        sec_uuid = libvirt.create_secret(auth_sec_dict, remote_args=remote_args)
        auth_sec_dict.update({"sec_uuid": sec_uuid})
        if session:
            session.secret_set_value(sec_uuid, secret_value, encode=True,
                                     debug=True, ignore_status=True)
            session.close_session()
        else:
            virsh.secret_set_value(sec_uuid, secret_value, encode=True, debug=True)

    _create_secret()
    params.update({"auth_sec_dict": auth_sec_dict})
    virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
                   'remote_pwd': remote_pwd, 'unprivileged_user': None,
                   'ssh_remote_auth': True}
    remote_virsh_session = None
    remote_virsh_session = virsh.VirshPersistent(**virsh_dargs)
    _create_secret(session=remote_virsh_session, remote_args=virsh_dargs)


def setup_vtpm(params, test, vm, migration_obj):
    """
    Setup vTPM device in guest xml

    :param params: dict, test parameters
    :param vm: VM object
    :param test: test object
    :param migration_obj: migration object
    """
    vm_name = params.get("migrate_main_vm")
    transient_vm = "yes" == params.get("transient_vm", "no")
    tpm_dict = eval(params.get('tpm_dict', '{}'))
    set_remote_libvirtd_log = "yes" == params.get("set_remote_libvirtd_log", "no")
    auth_sec_dict = params.get("auth_sec_dict")

    test.log.info("Setup vTPM device in guest xml.")
    if set_remote_libvirtd_log:
        migration_obj.set_remote_log()
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # Remove all existing tpm devices
    vmxml.remove_all_device_by_type('tpm')

    tpm_dict['backend']['encryption_secret'] = auth_sec_dict['sec_uuid']
    libvirt_vmxml.modify_vm_device(vmxml, 'tpm', tpm_dict)

    if transient_vm:
        virsh.undefine(vm_name, options='--nvram', debug=True, ignore_status=False)
        virsh.create(vmxml.xml, ignore_status=False, debug=True)
    else:
        vm.start()
    vm.wait_for_login().close()


def prepare_ceph_disk(params, test, vm):
    """
    Prepare ceph disk

    :param params: dict, test parameters
    :param vm: VM object
    :param test: test object
    """
    mon_host = params.get("mon_host")
    disk_source_name = params.get("disk_source_name")
    seclabel_dict = eval(params.get("seclabel_dict", "{}"))
    vm_name = params.get("migrate_main_vm")

    detected_distro = distro.detect()
    rbd_img_prefix = '_'.join(['rbd', detected_distro.name,
                               detected_distro.version,
                               detected_distro.release,
                               detected_distro.arch])
    disk_source_name = os.path.join(disk_source_name, rbd_img_prefix + '.img')
    params.update({"disk_source_name": disk_source_name})
    ceph.rbd_image_rm(mon_host, disk_source_name.split("/")[0],
                      disk_source_name.split("/")[1])
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml.set_seclabel([seclabel_dict])
    vmxml.sync()
    libvirt.set_vm_disk(vm, params)


def run(test, params, env):
    """
    Test migration with vtpm device with shared TPM state.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def setup_nfs():
        """
        Setup for nfs storage type

        """
        tpm_security_contexts = params.get("tpm_security_contexts")

        test.log.info("Setup for nfs storage type.")
        libvirt.set_vm_disk(vm, params)
        setup_vtpm(params, test, vm, migration_obj)
        check_tpm_security_context(params, vm, test, tpm_security_contexts)
        check_swtpm_process(params, test)
        check_vtpm_func(params, vm, test)

    def setup_ceph():
        """
        Setup for ceph storage type

        """
        tpm_security_contexts = params.get("tpm_security_contexts")
        mon_host = params.get("mon_host")
        set_remote_libvirtd_log = "yes" == params.get("set_remote_libvirtd_log", "no")

        test.log.info("Setup for ceph storage type.")
        if set_remote_libvirtd_log:
            migration_obj.set_remote_log()

        cmd = "mount -t ceph %s:6789:/  /var/lib/libvirt/swtpm -o name=admin" % mon_host
        process.run(cmd, ignore_status=False, shell=True)
        remote.run_remote_cmd(cmd, params)

        prepare_ceph_disk(params, test, vm)
        setup_vtpm(params, test, vm, migration_obj)
        check_tpm_security_context(params, vm, test, tpm_security_contexts)
        check_swtpm_process(params, test)
        check_vtpm_func(params, vm, test)

    def verify_test():
        """
        Verify steps

        """
        migration_obj.check_local_and_remote_log()
        check_vtpm_func(params, vm, test, remote=True)

    def verify_test_again():
        """
        Verify steps for migration back

        """
        tpm_security_contexts_restore = params.get("tpm_security_contexts_restore")
        check_vtpm_func(params, vm, test)
        vm.shutdown()
        vm.wait_for_shutdown()
        check_tpm_security_context(params, vm, test, tpm_security_contexts_restore)

    def cleanup_ceph():
        """
        Cleanup steps for ceph case

        """
        cmd = "umount /var/lib/libvirt/swtpm"
        process.run(cmd, ignore_status=False, shell=True)
        remote.run_remote_cmd(cmd, params)
        migration_obj.cleanup_connection()

    vm_name = params.get("migrate_main_vm")
    shared_storage_type = params.get('shared_storage_type', '')

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % shared_storage_type) if "setup_%s" % shared_storage_type in \
        locals() else migration_obj.setup_connection
    cleanup_test = eval("cleanup_%s" % shared_storage_type) if "cleanup_%s" % shared_storage_type in \
        locals() else migration_obj.cleanup_connection

    try:
        set_secret(params)
        setup_test()
        migration_obj.run_migration()
        verify_test()
        migration_obj.run_migration_back()
        verify_test_again()
    finally:
        cleanup_test()
