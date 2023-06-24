from provider.migration import base_steps


def run(test, params, env):
    """
    Test VM live migration with copy storage - network data transport - TCP - port reuse.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def run_migration_again():
        """
        Run migration again

        """
        dest_uri = params.get("virsh_migrate_desturi")

        test.log.debug("Run migration again.")
        vm.connect_uri = dest_uri
        if vm.is_alive():
            vm.destroy()
        vm.connect_uri = migration_obj.src_uri
        vm.start()
        vm.wait_for_login().close()

        migration_obj.run_migration_again()

    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        base_steps.prepare_disks_remote(params, vm)
        migration_obj.run_migration()
        if migrate_again:
            run_migration_again()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
        base_steps.cleanup_disks_remote(params, vm)
