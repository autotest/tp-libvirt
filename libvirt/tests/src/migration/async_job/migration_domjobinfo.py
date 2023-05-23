from virttest.utils_libvirt import libvirt_monitor

from provider.migration import base_steps


def run(test, params, env):
    """
    Test domjobinfo - migration succeeds.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_test():
        """
        Verify steps

        """
        expected_domjobinfo_complete = params.get("expected_domjobinfo_complete")

        migration_obj.verify_default()
        if expected_domjobinfo_complete:
            params.update({"domjobinfo_options": "--completed"})
            libvirt_monitor.check_domjobinfo_output(params)

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
