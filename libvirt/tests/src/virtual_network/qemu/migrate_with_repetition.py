from provider.migration import base_steps
from virttest import error_context


@error_context.context_aware
def run(test, params, env):
    """
    Test migration with repetition

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    vm = env.get_vm(params.get("main_vm"))
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        error_context.context(
            "Starting to migrate the guest to the destination", test.log.info
        )
        migration_obj.setup_connection()
        migration_obj.run_migration()

        migration_obj.migration_test.post_migration_check(
            [vm], params, dest_uri=params.get("virsh_migrate_desturi")
        )

        error_context.context(
            "Starting to migrate the guest with repetition", test.log.info
        )
        migrate_repeat_times = params.get_numeric("migrate_repeat_times", 2000)

        for idx in range(migrate_repeat_times):
            try:
                error_context.context(
                    f"Ping-pong migration iteration: {idx + 1}", test.log.info
                )
                if idx % 2 == 0:
                    migration_obj.run_migration_back()
                else:
                    migration_obj.run_migration()

            except Exception as e:
                test.fail(f"Migration failed at iteration {idx + 1}: {str(e)}")

    finally:
        migration_obj.cleanup_connection()
