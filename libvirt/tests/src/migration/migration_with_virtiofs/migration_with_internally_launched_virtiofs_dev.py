import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps


def run(test, params, env):
    """
    Verify that guest with internally launched virtiofs device can be migrated.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps.")
        vm_attrs = eval(params.get("vm_attrs", "{}"))
        fs_dict = eval(params.get("fs_dict", "{}"))
        dev_type = params.get("dev_type")
        mount_tag = params.get("mount_tag")
        mount_dir = params.get("mount_dir")
        expect_str = params.get("expect_str")

        migration_obj.setup_connection()

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.remove_all_device_by_type('tpm')
        vmxml.sync()
        if not vm.is_alive():
            vm.start()
        fs = libvirt_vmxml.create_vm_device_by_type(dev_type, fs_dict)
        virsh.attach_device(vm_name, fs.xml, debug=True, ignore_status=False)

        vm_session = vm.wait_for_login()
        vm_session.cmd_output_safe(f"mkdir {mount_dir}")
        vm_session.cmd_output_safe(f"mount -t virtiofs {mount_tag} {mount_dir}")
        output = vm_session.cmd_output("df -h")
        vm_session.close()
        test.log.debug("output: %s", output)
        if not re.search(expect_str, output):
            test.fail(f'Expect content "{expect_str}" not in output: {output}')

    def verify_test():
        """
        Verify steps for cases

        """
        test.log.info("Verify steps.")
        desturi = params.get("virsh_migrate_desturi")
        expect_str = params.get("expect_str")
        test_file = params.get("test_file")
        mnt_path_name = params.get("mnt_path_name")

        backup_uri, vm.connect_uri = vm.connect_uri, desturi
        vm_session = vm.wait_for_serial_login(timeout=120, recreate_serial_console=True)
        output = vm_session.cmd_output("df -h")
        test.log.debug("output: %s", output)
        if not re.search(expect_str, output):
            test.fail(f'Expect content "{expect_str}" not in output: {output}')
        cmd1 = f"dd if=/dev/zero of={test_file} bs=1M count=10; sync"
        vm_session.cmd_output(cmd1)
        cmd2 = f"md5sum {test_file}"
        ret_in_vm = vm_session.cmd_output(cmd2)
        test.log.debug("md5sum result: %s", ret_in_vm)
        vm_session.close()
        vm.connect_uri = backup_uri

        cmd3 = f"md5sum {mnt_path_name}/test_file"
        ret_in_host = process.run(cmd3, shell=True, ignore_status=False)
        if ret_in_vm.split()[0] != ret_in_host.stdout_text.split()[0]:
            test.fail("md5 value in host is different from in vm.")

        migration_obj.verify_default()

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
