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
        expected_domjobinfo_complete = eval(params.get("expected_domjobinfo_complete"))
        remote_ip = params.get("server_ip")
        postcopy_options = params.get("postcopy_options")

        test.log.info("Verify test.")
        migration_obj.verify_default()
        if expected_domjobinfo_complete:
            libvirt_monitor.check_domjobinfo_output(vm_name,
                                                    expected_domjobinfo_complete=expected_domjobinfo_complete,
                                                    options="--completed",
                                                    postcopy_options=postcopy_options,
                                                    remote_ip=remote_ip)

    vm_name = params.get("migrate_main_vm")

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

        migration_obj.setup_connection()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
