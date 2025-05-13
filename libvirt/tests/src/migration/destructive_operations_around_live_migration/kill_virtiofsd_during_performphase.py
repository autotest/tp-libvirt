import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import remote
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    This case is to verify that if killing virtiofsd process during PerformPhase of
    migration, migration will fail. If dst virtiofsd is killed, src virtiofsd will keep
    running. If src virtiofsd is killed, dst virtiofsd will be closed.

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

        cmd1 = f"chcon -t virtd_exec_t /usr/libexec/virtiofsd"
        cmd2 = f"mkdir -p {mnt_path_name}"
        cmd3 = f"systemd-run /usr/libexec/virtiofsd --socket-path={socket_path} -o source={mnt_path_name}"
        multi_cmd1 = f"{cmd1}; {cmd2}; {cmd3}"
        process.run(multi_cmd1, shell=True, ignore_status=False)
        remote.run_remote_cmd(multi_cmd1, params, ignore_status=False)
        cmd4 = f"chcon -t svirt_image_t {socket_path}"
        cmd5 = f"chown qemu:qemu {socket_path}"
        multi_cmd2 = f"{cmd4}; {cmd5}"
        process.run(multi_cmd2, shell=True, ignore_status=False)
        remote.run_remote_cmd(multi_cmd2, params, ignore_status=False)

        migration_obj.setup_connection()

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.remove_all_device_by_type("tpm")
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
        Verify migration result

        """
        dest_uri = params.get("virsh_migrate_desturi")
        expected_src_state = params.get("expected_src_state")
        expected_dest_state = params.get("expected_dest_state")

        func_returns = dict(migration_obj.migration_test.func_ret)
        migration_obj.migration_test.func_ret.clear()
        test.log.debug("Migration returns function results: %s", func_returns)
        virsh.domstate(vm_name, uri=migration_obj.src_uri, debug=True)
        if expected_src_state:
            if not libvirt.check_vm_state(
                vm.name, expected_src_state, uri=migration_obj.src_uri
            ):
                test.fail(
                    "Migrated VM failed to be in %s state at source."
                    % expected_src_state
                )
        if expected_dest_state and expected_dest_state == "nonexist":
            virsh.domstate(vm_name, uri=dest_uri, debug=True)
            if virsh.domain_exists(vm_name, uri=dest_uri):
                test.fail("The domain on target host is found, but expected not")

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
