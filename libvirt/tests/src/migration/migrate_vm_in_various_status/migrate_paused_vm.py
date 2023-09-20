from virttest import virsh

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

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        # Pause the guest
        virsh.suspend(vm.name, debug=True, ignore_status=False)
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
