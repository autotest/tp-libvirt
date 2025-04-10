from provider.migration import base_steps


def run(test, params, env):
    """
    To verify that the ip and port for copying storage can be specified by
    --migrateuri and --disks-port respectively.
    This cases starts vm with local storage, then do live migration with
    copying storage with virsh options --migrateuri and --disks-port,
    and checks the migration ip and port during migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")
    migrateuri_port = params.get("migrateuri_port")
    disks_port = params.get("disks_port")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        if migrateuri_port:
            migration_obj.remote_add_or_remove_port(migrateuri_port)
        if disks_port:
            migration_obj.remote_add_or_remove_port(disks_port)
        base_steps.prepare_disks_remote(params, vm)
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        if migrateuri_port:
            migration_obj.remote_add_or_remove_port(migrateuri_port, add=False)
        if disks_port:
            migration_obj.remote_add_or_remove_port(disks_port, add=False)
        migration_obj.cleanup_connection()
        base_steps.cleanup_disks_remote(params, vm)
