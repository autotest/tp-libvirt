from provider.migration import base_steps


def run(test, params, env):
    """
    Test recover migration when it is not paused.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        migration_obj.run_migration()
        if migrate_again:
            migration_obj.run_migration_again()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
