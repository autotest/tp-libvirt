from provider.migration import base_steps


def run(test, params, env):
    """
    Test async job:

    1.  abort_migration_with_wrong_api_flag case: To verify that precopy
    migration can't be aborted by domjobabort with --postcopy, postcopy
    migration can't be aborted by domjobabort without --postcopy.

    """
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
