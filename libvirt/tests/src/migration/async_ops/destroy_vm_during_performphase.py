from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    This case is to verify that if destroying vm during PerformPhase of precopy
    migration, migration will fail.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_test():
        """
        Verify steps
        """
        expected_dest_state = params.get("expected_dest_state")
        expected_src_state = params.get("expected_src_state")
        dest_uri = params.get("virsh_migrate_desturi")

        test.log.info("Verify steps.")
        if not libvirt.check_vm_state(vm.name, expected_src_state, uri=migration_obj.src_uri):
            test.fail(f"Check of expected VM state ({expected_src_state}) on source host failed.")
        dest_vm_list = virsh.dom_list(options="--all --persistent", debug=True, uri=dest_uri)
        if expected_dest_state == "nonexist":
            if vm_name in dest_vm_list.stdout.strip():
                test.fail("%s should not exist." % vm_name)

    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")

    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        verify_test()
        if migrate_again:
            migration_obj.run_migration_again()
            migration_obj.verify_default()
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)
    finally:
        migration_obj.cleanup_connection()
