from virttest import libvirt_version
from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    Test live migration with vm configuration and status.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_suspend_target_vm():
        """
        Verify for suspend target vm
        """
        dest_uri = params.get("virsh_migrate_desturi")
        check_dest_state = params.get("check_dest_state")
        if not libvirt.check_vm_state(vm.name, check_dest_state, uri=dest_uri):
            test.fail("Migrated VMs failed to be in %s state at destination" % check_dest_state)
        virsh.resume(vm_name, uri=dest_uri, ignore_status=False, debug=True)
        migration_obj.verify_default()

    libvirt_version.is_libvirt_feature_supported(params)

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    verify_test = eval("verify_%s" % test_case) if "verify_%s" % test_case in \
        locals() else migration_obj.verify_default

    try:
        migration_obj.setup_connection()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
