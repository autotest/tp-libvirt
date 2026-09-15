from avocado.utils import process

from virttest import libvirt_version
from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    Test live migration with tcp transport.

    """

    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps.")
        migration_obj.setup_connection()
        if test_case == "tcp_common":
            process.run("echo > {}".format(qemu_log), shell=True)

    def verify_test():
        """
        Verify steps

        """
        test.log.info("Verify steps.")
        migration_obj.verify_default()
        if test_case == "tcp_common":
            libvirt.check_logfile("shutting down, reason=migrated", qemu_log, str_in_log=True)

    libvirt_version.is_libvirt_feature_supported(params)

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    qemu_log = "/var/log/libvirt/qemu/%s.log" % vm_name
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
    finally:
        migration_obj.cleanup_connection()
