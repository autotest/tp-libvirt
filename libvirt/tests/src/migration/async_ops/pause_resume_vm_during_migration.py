from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    This case is to verify that during migration, vm:
        can be paused by user on src host during PerformPhase of precopy
        can't be resumed by user on src/target host during PerformPhase of precopy
        can't be paused by user on target host during FinishPhase of postcopy
        can't be resumed by user on src host during FinishPhase of postcopy

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm_name = params.get("migrate_main_vm")

    virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        virsh_session, _ = migration_base.monitor_event(params)
        migration_obj.run_migration()
        migration_obj.verify_default()
        migration_base.check_event_output(params, test, virsh_session)
    finally:
        migration_obj.cleanup_connection()
