from virttest import virsh

from virttest.utils_test import libvirt_domjobinfo

from provider.migration import base_steps


def run(test, params, env):
    """
    Test cases about maxdowntime.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_default_maxdowntime():
        """
        Setup for default_maxdowntime case

        """
        test.log.debug("Setup for default_maxdowntime case.")
        compared_value = params.get("compared_value")

        maxdowntime = virsh.migrate_getmaxdowntime(vm_name).stdout.strip()
        if maxdowntime != compared_value:
            test.fail("Get default maxdowntime error: %s" % maxdowntime)
        migration_obj.setup_connection()

    def setup_set_maxdowntime_before_mig():
        """
        Setup for set_maxdowntime_before_mig case

        """
        test.log.debug("Setup for set_maxdowntime_before_mig case.")
        compared_value = params.get("compared_value")

        ret = virsh.migrate_setmaxdowntime(vm_name, compared_value, debug=True)
        if ret.exit_status:
            test.fail("Set maxdowntime before migration failed.")
        maxdowntime = virsh.migrate_getmaxdowntime(vm_name).stdout.strip()
        if maxdowntime != compared_value:
            test.fail("Get maxdowntime error: %s" % maxdowntime)
        migration_obj.setup_connection()

    def verify_maxdowntime():
        """
        Verify maxdowntime after migration

        """
        test.log.debug("Verify maxdowntime after migration.")
        params.update({'jobinfo_item': 'Total downtime:'})
        libvirt_domjobinfo.check_domjobinfo(vm, params, option="--completed")
        migration_obj.verify_default()

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    params.update({'vm_obj': vm})
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection

    try:
        setup_test()
        migration_obj.run_migration()
        verify_maxdowntime()
    finally:
        migration_obj.cleanup_connection()
