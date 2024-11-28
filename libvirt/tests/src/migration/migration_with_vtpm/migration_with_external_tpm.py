import os
import shutil

from avocado.utils import process

from virttest import remote
from virttest import utils_package
from virttest import utils_misc
from virttest import virsh
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps


def check_tpm_security_context(params, test, expected_contexts, on_remote=False):
    """
    Check TPM state file security contexts

    :param params: dict, test parameters
    :param test: test object
    :param expected_contexts: expected tpm security context
    :param on_remote: True to check context on remote
    """
    statedir = params.get("statedir")

    test.log.debug("Check tpm security context: (on_remote: %s)", on_remote)
    cmd = "ls -lZ %s/tpm2-00.permall" % statedir
    if on_remote:
        cmd_result = remote.run_remote_cmd(cmd, params)
    else:
        process.run("ls -lZd %s" % statedir)
        cmd_result = process.run(cmd, ignore_status=True, shell=True)
    if cmd_result.exit_status:
        test.error("Fail to run '%s'." % cmd)
    if expected_contexts not in cmd_result.stdout_text:
        test.log.warn("Fail to find '%s' in '%s'." % (expected_contexts, cmd_result.stdout))


def check_vtpm_func(params, vm, test, on_remote=False):
    """
    Check vtpm function

    :param params: dict, test parameters
    :param vm: VM object
    :param test: test object
    :param on_remote: True to check vtpm function on remote
    """
    tpm_cmd = params.get("tpm_cmd")
    dest_uri = params.get("virsh_migrate_desturi")
    src_uri = params.get("virsh_migrate_connect_uri")
    test.log.debug("Check vtpm func: (on_remote: %s).", on_remote)
    if on_remote:
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
    if on_remote:
        if vm.serial_console is not None:
            vm.cleanup_serial_console()
        vm.connect_uri = src_uri
    if cmd_result:
        test.fail("Fail to run '%s': %s." % (tpm_cmd, cmd_result))


def launch_external_swtpm(params, test, skip_setup=False, on_remote=False):
    """
    Launch externally swtpm

    :param params: dict, test parameters
    :param test: test object
    :param skip_setup: whether skip swtpm_setup steps
    :param on_remote: True to execute on remote
    """
    tpm_dict = eval(params.get('tpm_dict', '{}'))
    source_socket = tpm_dict['backend']['source']['path']
    statedir = params.get("statedir")
    swtpm_setup_path = params.get("swtpm_setup_path")
    swtpm_path = params.get("swtpm_path")

    test.log.info("Launch external swtpm process: (on_remote: %s)",  on_remote)
    if not skip_setup:
        cmd1 = 'chcon -t virtd_exec_t %s' % swtpm_setup_path
        cmd2 = 'chcon -t virtd_exec_t %s' % swtpm_path
        if on_remote:
            remote.run_remote_cmd("rm -rf %s" % statedir, params)
            remote.run_remote_cmd("mkdir %s" % statedir, params)
            remote.run_remote_cmd(cmd1, params)
            remote.run_remote_cmd(cmd2, params)
        else:
            if os.path.exists(statedir):
                shutil.rmtree(statedir)
            os.mkdir(statedir)
            process.run("ls -lZd %s" % statedir)
            process.run(cmd1, ignore_status=False, shell=True)
            process.run(cmd2, ignore_status=False, shell=True)
        cmd3 = "systemd-run %s --tpm2 --tpmstate %s --create-ek-cert --create-platform-cert --overwrite" % (swtpm_setup_path, statedir)
    cmd4 = "systemd-run %s socket --ctrl type=unixio,path=%s,mode=0600 --tpmstate dir=%s,mode=0600 --tpm2 --terminate" % (swtpm_path, source_socket, statedir)
    try:
        if not skip_setup:
            if on_remote:
                remote.run_remote_cmd(cmd3, params)
            else:
                process.run(cmd3, ignore_status=False, shell=True)
        if on_remote:
            remote.run_remote_cmd(cmd4, params)
            remote.run_remote_cmd('chcon -t svirt_image_t %s' % source_socket, params)
            remote.run_remote_cmd('chown qemu:qemu %s' % source_socket, params)
        else:
            process.run(cmd4, ignore_status=False, shell=True)
            # Make sure the socket is created
            utils_misc.wait_for(lambda: os.path.isdir(source_socket), timeout=3)
            process.run('chcon -t svirt_image_t %s' % source_socket, ignore_status=False, shell=True)
            process.run('chown qemu:qemu %s' % source_socket, ignore_status=False, shell=True)
    except Exception as err:
        if on_remote:
            remote.run_remote_cmd('pkill swtpm', params)
        else:
            process.run("pkill swtpm", shell=True)
        test.error("{}".format(err))


def setup_vtpm(params, test, vm):
    """
    Setup vTPM device in guest xml

    :param params: dict, test parameters
    :param vm: VM object
    :param test: test object
    """
    vm_name = params.get("migrate_main_vm")
    transient_vm = "yes" == params.get("transient_vm", "no")
    tpm_dict = eval(params.get('tpm_dict', '{}'))

    test.log.info("Setup vTPM device in guest xml.")
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # Remove all existing tpm devices
    try:
        vmxml.remove_all_device_by_type('tpm')
        libvirt_vmxml.modify_vm_device(vmxml, 'tpm', tpm_dict)
    except Exception as e:
        test.error("Error occurred when set vtpm dev in guest xml: %s" % e)

    if transient_vm:
        virsh.undefine(vm_name, options='--nvram', debug=True, ignore_status=False)
        virsh.create(vmxml.xml, ignore_status=False, debug=True)
    else:
        vm.start()
    test.log.debug("vm xml after vtpm setup is %s", vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))
    vm.wait_for_login().close()


def run(test, params, env):
    """
    Test migration with external vtpm device.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def setup_test():
        """
        Setup steps

        """
        tpm_security_contexts = params.get("tpm_security_contexts")

        libvirt.set_vm_disk(vm, params)
        launch_external_swtpm(params, test)
        launch_external_swtpm(params, test, skip_setup=False, on_remote=True)
        setup_vtpm(params, test, vm)
        check_tpm_security_context(params, test, tpm_security_contexts)
        check_vtpm_func(params, vm, test)

    def verify_test():
        """
        Verify steps

        """
        check_vtpm_func(params, vm, test, on_remote=True)

    def verify_test_again():
        """
        Verify steps for migration back

        """
        tpm_security_contexts = params.get("tpm_security_contexts")
        migrate_vm_back = "yes" == params.get("migrate_vm_back", "yes")
        if not migrate_vm_back:
            return

        check_vtpm_func(params, vm, test)
        vm.shutdown()
        vm.wait_for_shutdown()
        check_tpm_security_context(params, test, tpm_security_contexts)

    def cleanup_test():
        """
        Cleanup steps

        """
        swtpm_setup_path = params.get("swtpm_setup_path")
        swtpm_path = params.get("swtpm_path")
        statedir = params.get("statedir")

        cmd1 = "restorecon %s" % swtpm_setup_path
        cmd2 = "restorecon %s" % swtpm_path
        process.run(cmd1, ignore_status=False, shell=True)
        process.run(cmd2, ignore_status=False, shell=True)
        if os.path.exists(statedir):
            shutil.rmtree(statedir)
        remote.run_remote_cmd(cmd1, params)
        remote.run_remote_cmd(cmd2, params)
        remote.run_remote_cmd("rm -rf %s" % statedir, params)
        migration_obj.cleanup_connection()

    vm_name = params.get("migrate_main_vm")

    libvirt_version.is_libvirt_feature_supported(params)
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
        launch_external_swtpm(params, test, skip_setup=True)
        migration_obj.run_migration_back()
        verify_test_again()
    finally:
        cleanup_test()
