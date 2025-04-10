import aexpect

from virttest import remote
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    Verify panic device works well after migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup step

        """
        panic_model = params.get("panic_model")
        addr_type = params.get("addr_type")
        addr_iobase = params.get("addr_iobase")
        dump_file_path = params.get("dump_file_path")
        crash_action = params.get("crash_action")
        panic_dev = eval(params.get("panic_dev"))

        test.log.info("Setup steps.")
        migration_obj.setup_connection()
        # Cleanup dump file
        cmd = "rm -f %s/*" % dump_file_path
        remote.run_remote_cmd(cmd, params)

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        libvirt_vmxml.modify_vm_device(vmxml, 'panic', panic_dev)
        vmxml.on_crash = crash_action
        vmxml.sync()
        test.log.info("Guest xml now is: %s", vmxml)
        vm.start()
        vm.wait_for_login().close()

    def verify_test():
        """
        Verify step

        """
        dest_uri = params.get("virsh_migrate_desturi")
        expected_match = params.get("expected_match")
        dump_file_path = params.get("dump_file_path")

        test.log.info("Verify steps.")
        migration_obj.verify_default()

        backup_uri, vm.connect_uri = vm.connect_uri, dest_uri
        vm.cleanup_serial_console()
        vm.create_serial_console()
        remote_vm_session = vm.wait_for_serial_login(timeout=360)
        try:
            remote_vm_session.cmd("systemctl stop kdump", ignore_all_errors=True)
            remote_vm_session.cmd("echo 1 > /proc/sys/kernel/sysrq")
            remote_vm_session.cmd_status_output("echo c > /proc/sysrq-trigger", timeout=3)
        except aexpect.ShellTimeoutError:
            # This is expected
            pass
        except Exception:
            # This is unexpected
            raise

        remote_vm_session.close()
        ret = virsh.domstate(vm_name, extra="--reason", uri=dest_uri, debug=True)
        libvirt.check_result(ret, expected_match=expected_match)

        # Check dump file
        cmd = "ls %s" % dump_file_path
        ret = remote.run_remote_cmd(cmd, params, ignore_status=False)
        if vm_name not in ret.stdout_text.strip():
            test.fail("Not found dump file.")

        vm.destroy()
        vm.connect_uri = backup_uri

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
