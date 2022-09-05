from virttest import virsh

from provider.migration import base_steps


def run(test, params, env):
    """
    Test memory compression.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_auto_converge():
        """
        Setup for auto converge

        """
        test.log.info("Setup maxdowntime.")
        # Set maxdowntime to small value, then migration won't converge too fast.
        virsh.migrate_setmaxdowntime(vm_name, "100", debug=True)
        migration_obj.setup_connection()

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    params.update({'vm_obj': vm})
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection
    verify_test = eval("verify_%s" % test_case) if "verify_%s" % test_case in \
        locals() else migration_obj.verify_default

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
