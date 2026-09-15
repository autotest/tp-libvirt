from virttest import virsh

from provider.migration import base_steps


def run(test, params, env):
    """
    Test VM live migration - persist target vm and undefine src vm.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps
        """
        persistent_option = params.get("persistent_option")
        extra = params.get("virsh_migrate_extra")

        test.log.info("Setup test.")
        if persistent_option:
            extra = "%s %s" % (extra, persistent_option)
            params.update({"virsh_migrate_extra": extra})

        migration_obj.setup_connection()

    def verify_test():
        """
        Verify steps
        """
        desturi = params.get("virsh_migrate_desturi")

        test.log.info("Verify test.")
        if virsh.domain_exists(vm_name, uri=desturi):
            test.fail("The domain on target host is found, but expected not.")

        output = virsh.dom_list("--persistent", debug=True).stdout.strip()
        if vm_name not in output:
            test.fail("Source vm is not persistent.")

    def verify_again():
        """
        Verify again
        """
        desturi = params.get("virsh_migrate_desturi")
        persistent_option = params.get("persistent_option")
        extra = params.get("virsh_migrate_extra")

        test.log.info("Verify again.")
        if "undefinesource" in extra:
            if virsh.domain_exists(vm_name):
                test.fail("The domain on source host is found, but expected not")

        if persistent_option:
            output = virsh.dom_list("--persistent", debug=True, uri=desturi).stdout.strip()
            if vm_name not in output:
                test.fail("target vm is not persistent.")

    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        # Ensure the guest CPU is compatible with the destination host before
        # migration. When the source and destination CPUs differ, compute a
        # migratable baseline and apply it; the guest must be off to rewrite
        # its CPU, so it is (re)started below. No-op when the CPUs already
        # match and on aarch64.
        if not base_steps.check_cpu_for_mig(params):
            if vm.is_alive():
                vm.destroy()
            base_steps.sync_cpu_for_mig(params)

        setup_test()
        migration_obj.run_migration()
        verify_test()
        if migrate_again:
            migration_obj.run_migration_again()
            verify_again()
    finally:
        migration_obj.cleanup_connection()
