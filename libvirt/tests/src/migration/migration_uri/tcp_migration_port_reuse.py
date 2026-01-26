from provider.migration import base_steps


def run(test, params, env):
    """
    Test live migration with tcp transport - migration port reuse.

    """

    def run_migration_again():
        """
        Run migration again

        """
        test_case = params.get("test_case")

        if test_case == "migration_completion":
            dest_uri = params.get("virsh_migrate_desturi")
            vm.connect_uri = dest_uri
            if vm.is_alive():
                vm.destroy()
            vm.connect_uri = migration_obj.src_uri
            if not vm.is_alive():
                vm.start()
            vm.wait_for_login().close()

        migration_obj.run_migration_again()

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        migration_obj.run_migration()
        run_migration_again()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
