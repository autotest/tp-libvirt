import os

from avocado.utils import process

from virttest import remote
from virttest import utils_package
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_secret
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps

src_sec_uuid = None
dst_sec_uuid = None


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
    src_secret_value = params.get("src_secret_value")
    src_secret_value_path = params.get("src_secret_value_path")
    dst_secret_value = params.get("dst_secret_value")
    dst_secret_value_path = params.get("dst_secret_value_path")
    remote_pwd = params.get("migrate_dest_pwd")
    remote_ip = params.get("migrate_dest_host")
    remote_user = params.get("remote_user", "root")

    def _create_secret(session=None, remote_args=None, secret_value_path=None):
        """
        create secret

        :param session: a session object of remote host
        :param remote_args: remote host parameters
        :param secret_value_path: the path of secret value
        """
        libvirt_secret.clean_up_secrets(session)
        global dst_sec_uuid
        global src_sec_uuid
        if remote_args:
            dst_sec_uuid = libvirt.create_secret(auth_sec_dict, remote_args=remote_args)
        else:
            src_sec_uuid = libvirt.create_secret(auth_sec_dict)
            auth_sec_dict.update({"sec_uuid": src_sec_uuid})
        if os.path.exists(secret_value_path):
            os.remove(secret_value_path)
        if session:
            cmd = "echo '%s' > %s" % (dst_secret_value, secret_value_path)
            process.run(cmd, shell=True)
            remote.scp_to_remote(remote_ip, '22', remote_user, remote_pwd,
                                 secret_value_path, secret_value_path,
                                 limit="", log_filename=None, timeout=60,
                                 interface=None)
            cmd = f"virsh secret-set-value {dst_sec_uuid} --file {secret_value_path} --plain"
            remote.run_remote_cmd(cmd, params)
        else:
            cmd = "echo '%s' > %s" % (src_secret_value, secret_value_path)
            process.run(cmd, shell=True)
            cmd = f"virsh secret-set-value {src_sec_uuid} --file {secret_value_path} --plain"
            process.run(cmd, shell=True)

    _create_secret(secret_value_path=src_secret_value_path)
    params.update({"auth_sec_dict": auth_sec_dict})
    virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
                   'remote_pwd': remote_pwd, 'unprivileged_user': None,
                   'ssh_remote_auth': True}
    remote_virsh_session = None
    remote_virsh_session = virsh.VirshPersistent(**virsh_dargs)
    _create_secret(session=remote_virsh_session, remote_args=virsh_dargs,
                   secret_value_path=dst_secret_value_path)


def setup_vtpm(params, test, vm, migration_obj):
    """
    Setup vTPM device in guest xml

    :param params: dict, test parameters
    :param vm: VM object
    :param test: test object
    :param migration_obj: migration object
    """
    vm_name = params.get("migrate_main_vm")
    tpm_dict = eval(params.get('tpm_dict', '{}'))
    auth_sec_dict = params.get("auth_sec_dict")

    test.log.info("Setup vTPM device in guest xml.")
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # Remove all existing tpm devices
    vmxml.remove_all_device_by_type('tpm')

    tpm_dict['backend']['encryption_secret'] = auth_sec_dict['sec_uuid']
    libvirt_vmxml.modify_vm_device(vmxml, 'tpm', tpm_dict)

    vm.start()
    vm.wait_for_login().close()


def check_ownership(swtpm_log, expected_ownership, test):
    """
    Check ownership

    :param swtpm_log: swtpm log path
    :param expected_ownership: Expected ownership
    :param test: test object
    """
    cmd = "ls -lZ %s" % swtpm_log
    ret = process.run(cmd, shell=True).stdout_text.strip()
    if expected_ownership not in ret:
        test.fail("Swtpm log ownership not correct: %s" % ret)


def run(test, params, env):
    """
    Test migration with vtpm device.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps.")
        set_secret(params)
        migration_obj.setup_connection()
        setup_vtpm(params, test, vm, migration_obj)

    def verify_test():
        """
        Verify steps

        """
        swtpm_log = params.get("swtpm_log")

        test.log.info("Verify steps.")
        check_ownership(swtpm_log, "svirt_image_t", test)
        virsh.shutdown(vm_name, debug=True)
        if not vm.wait_for_shutdown():
            test.error('VM failed to shutdown in 60s')
        check_ownership(swtpm_log, "virt_log_t", test)

    def cleanup_test():
        """
        Cleanup steps

        """
        desturi = params.get("virsh_migrate_desturi")
        swtpm_path = params.get("swtpm_path")
        dst_secret_value_path = params.get("dst_secret_value_path")
        src_secret_value_path = params.get("src_secret_value_path")

        test.log.info("Cleanup steps.")
        global src_sec_uuid
        if src_sec_uuid:
            virsh.secret_undefine(src_sec_uuid, debug=True, ignore_status=True)
        global dst_sec_uuid
        if dst_sec_uuid:
            virsh.secret_undefine(dst_sec_uuid, debug=True, ignore_status=True, uri=desturi)

        migration_obj.cleanup_connection()
        cmd = f"rm -rf {swtpm_path}/*"
        remote.run_remote_cmd(cmd, params)
        cmd = f"rm -rf {dst_secret_value_path}"
        remote.run_remote_cmd(cmd, params)
        process.run(cmd, shell=True, ignore_status=True)
        cmd = f"rm -rf {src_secret_value_path}"
        process.run(cmd, shell=True, ignore_status=True)

    vm_name = params.get("migrate_main_vm")
    test_case = params.get("test_case", "")
    migrate_again = "yes" == params.get("migrate_again", "no")
    desturi = params.get("virsh_migrate_desturi")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    if base_steps.check_cpu_for_mig(desturi):
        if test_case != "diff_secret_on_src_and_dst":
            test.cancel("Need to use machines with different cpu to test this case.")
    else:
        if test_case == "diff_secret_on_src_and_dst":
            base_steps.sync_cpu_for_mig(params)

    try:
        setup_test()
        migration_obj.run_migration()
        if test_case != "negative":
            verify_test()
            base_steps.sync_cpu_for_mig(params)
        else:
            migration_obj.verify_default()
        if migrate_again:
            migration_obj.run_migration_again()
            migration_obj.verify_default()
            check_vtpm_func(params, vm, test, remote=True)
    finally:
        cleanup_test()
