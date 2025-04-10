from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    To verify that vm live migration with copying storage can be aborted
    successfully, and related resource can be cleaned up so next migration
    can succeed.

    """
    def verify_test():
        """
        Verify test

        """
        migration_obj.verify_default()
        # Check event output
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)

    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")

    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        base_steps.prepare_disks_remote(params, vm)
        # Monitor event on source/target host
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        if migrate_again:
            migration_obj.run_migration_again()
        verify_test()
    finally:
        base_steps.cleanup_disks_remote(params, vm)
        migration_obj.cleanup_connection()
