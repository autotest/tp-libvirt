from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    This case is to verify that if killing qemu process during FinishPhase of
    postcopy migration, migration will fail.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_test():
        """
        Verify migration result

        """
        dest_uri = params.get("virsh_migrate_desturi")
        expected_src_state = params.get("expected_src_state")
        expected_dest_state = params.get("expected_dest_state")

        func_returns = dict(migration_obj.migration_test.func_ret)
        migration_obj.migration_test.func_ret.clear()
        test.log.debug("Migration returns function results: %s", func_returns)
        if expected_src_state:
            if not libvirt.check_vm_state(vm.name, expected_src_state, uri=migration_obj.src_uri):
                test.fail("Migrated VM failed to be in %s state at source." % expected_src_state)
        if expected_dest_state and expected_dest_state == "nonexist":
            virsh.domstate(vm_name, uri=dest_uri, debug=True)
            if virsh.domain_exists(vm_name, uri=dest_uri):
                test.fail("The domain on target host is found, but expected not")
        if expected_src_state == "shut off":
            vm.start()
            vm.wait_for_login().close()
        elif expected_src_state == "paused":
            vm.destroy()
            vm.start()
            vm.wait_for_login().close()

        if expected_dest_state == "running":
            virsh.destroy(vm_name, uri=dest_uri)

    vm_name = params.get("migrate_main_vm")
    test_case = params.get("test_case", "")
    migrate_again = "yes" == params.get("migrate_again", "no")

    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection
    migration_test_again = eval("migration_%s_again" % test_case) if "migration_%s_again" % test_case in \
        locals() else migration_obj.run_migration_again

    try:
        setup_test()
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        verify_test()
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)
        if migrate_again:
            migration_test_again()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
