from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test network bandwidth - postcopy.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_default_bandwidth():
        """
        Setup for default_bandwidth

        """
        compare_to_value = params.get("compare_to_value")

        test.log.debug("Setup for default_bandwidth.")
        migration_obj.setup_connection()
        speed = virsh.migrate_getspeed(vm_name, debug=True).stdout.strip()
        if compare_to_value != speed:
            test.fail("Default bandwidth is not 0.")

    def setup_set_bandwidth_when_vm_running():
        """
        Setup bandwidth when vm running

        """
        test.log.debug("Setup bandwidth when vm running.")
        migration_obj.setup_connection()
        migration_base.set_bandwidth(params)

    def verify_test():
        """
        Verify steps

        """
        check_postcopy_log = params.get("check_postcopy_log")
        log_file = params.get("libvirtd_debug_file")

        test.log.debug("Verify steps.")
        migration_obj.verify_default()
        if check_postcopy_log:
            libvirt.check_logfile(check_postcopy_log, log_file)
        migration_base.check_event_output(params, test, virsh_session)

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")

    virsh_session = None

    vm = env.get_vm(vm_name)
    params.update({'vm_obj': vm})
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection

    try:
        virsh_session, _ = migration_base.monitor_event(params)
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
