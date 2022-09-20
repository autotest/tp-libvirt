from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    Test desturi of libvirt layer.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def verify_desturi():
        """
        Verify for desturi
        """
        vm_state_src = params.get("virsh_migrate_src_state")
        if not libvirt.check_vm_state(vm_name, vm_state_src):
            test.fail("Migrated VM failed to be in %s state at source" % vm_state_src)
        test.log.info("Guest state is '%s' at source is as expected", vm_state_src)
        migration_obj.verify_default()

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
