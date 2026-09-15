from virttest import virsh
from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    To verify that:
      migrating a paused vm can succeed and vm is in paused status on target host.
      migrating a paused vm can be canceled, and vm is in paused status on src host.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")
    migrate_vm_back = "yes" == params.get("migrate_vm_back", "no")

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
        # Pause the guest
        virsh.suspend(vm.name, debug=True, ignore_status=False)
        migration_obj.run_migration()
        migration_obj.verify_default()
        if migrate_vm_back:
            migration_obj.run_migration_back()
            if not libvirt.check_vm_state(vm_name, params.get("src_state"),
                                          uri=migration_obj.src_uri, debug=True):
                test.fail("Check vm state failed.")
    finally:
        migration_obj.cleanup_connection()
