from provider.migration import base_steps


def run(test, params, env):
    """
    Test VM live migration with copy storage - network data transport - UNIX+Proxy.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        base_steps.prepare_disks_remote(params, vm)
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
        base_steps.cleanup_disks_remote(params, vm)
