import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import remote
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps


def run(test, params, env):
    """
    Verify that guest with externally launched virtiofs device can be migrated.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps.")
        mnt_path_name = params.get("mnt_path_name")
        socket_path = params.get("socket_path")
        vm_attrs = eval(params.get("vm_attrs", "{}"))
        fs_dict = eval(params.get("fs_dict", "{}"))
        dev_type = params.get("dev_type")
        mount_tag = params.get("mount_tag")
        mount_dir = params.get("mount_dir")
        expect_str = params.get("expect_str")

        cmd1 = f"mkdir -p {mnt_path_name}"
        cmd2 = f"nohup /usr/libexec/virtiofsd --socket-path={socket_path} -o source={mnt_path_name} --log-level off > /dev/null 2>&1 & disown"
        multi_cmd1 = f"{cmd1}; {cmd2}"
        process.run(multi_cmd1, shell=True, ignore_status=False, ignore_bg_processes=True)
        remote.run_remote_cmd(multi_cmd1, params, ignore_status=False)
        cmd3 = f"chcon -t svirt_image_t {socket_path}"
        cmd4 = f"chown qemu:qemu {socket_path}"
        multi_cmd2 = f"{cmd3}; {cmd4}"
        process.run(multi_cmd2, shell=True, ignore_status=False)
        remote.run_remote_cmd(multi_cmd2, params, ignore_status=False)

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

        backup_uri, vm.connect_uri = vm.connect_uri, desturi
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(timeout=120)
        output = vm_session.cmd_output("df -h")
        vm_session.close()
        test.log.debug("output: %s", output)
        if not re.search(expect_str, output):
            test.fail(f'Expect content "{expect_str}" not in output: {output}')
        vm.connect_uri = backup_uri
        migration_obj.verify_default()

    def cleanup_test():
        """
        Cleanup steps for cases

        """
        socket_path = params.get("socket_path")

        migration_obj.cleanup_connection()
        remote.run_remote_cmd('pkill virtiofsd', params, ignore_status=True)
        remote.run_remote_cmd(f"rm -rf {socket_path}", params, ignore_status=True)
        process.run("pkill virtiofsd", shell=True, ignore_status=True)
        process.run(f"rm -rf {socket_path}", shell=True, ignore_status=True)

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        cleanup_test()
