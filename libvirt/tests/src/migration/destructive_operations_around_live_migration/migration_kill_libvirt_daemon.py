import json

from virttest import libvirt_version
from virttest import utils_split_daemons
from virttest import virsh

from virttest.utils_libvirt import libvirt_memory
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test live migration - kill libvirt daemon during PerformPhase of migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_zerocopy_option():
        """
        Setup for zerocopy_option case

        """
        test.log.info("Setup for zerocopy_option case.")
        original_value = libvirt_memory.get_qemu_process_memlock_hard_limit()
        old_hard_limit.append(original_value)
        params.update({'compared_hard_limit': original_value})
        migration_obj.setup_connection()

    def verify_test():
        """
        Verify migration result

        """
        dest_uri = params.get("virsh_migrate_desturi")
        check_migration_params = eval(params.get("check_migration_params"))
        expected_src_state = params.get("expected_src_state")
        expected_dest_state = params.get("expected_dest_state")

        func_returns = dict(migration_obj.migration_test.func_ret)
        migration_obj.migration_test.func_ret.clear()
        test.log.debug("Migration returns function results: %s", func_returns)
        if expected_src_state:
            if not libvirt.check_vm_state(vm.name, expected_src_state, uri=migration_obj.src_uri):
                test.fail("Migrated VM failed to be in %s state at source." % expected_src_state)
        if expected_dest_state and expected_dest_state == "nonexist":
            if virsh.domain_exists(vm_name, uri=dest_uri):
                test.fail("The domain on target host is found, but expected not")
        # Check disk on source
        migration_obj.migration_test.post_migration_check([vm], params)

        if check_migration_params:
            ret = virsh.qemu_monitor_command(vm_name, '{"execute":"query-migrate-parameters"}', '--pretty')
            libvirt.check_exit_status(ret)
            json_result = json.loads(ret.stdout_text)
            params_list = json_result['return']
            for key, value in check_migration_params.items():
                if int(value) != params_list[key]:
                    test.fail("Migration params %s change to %s." % (key, params_list[key]))
        migration_base.set_migrate_speed_to_high(params)

    def migration_zerocopy_option_again():
        """
        Migration zerocopy_option case again

        """
        if not vm.is_alive():
            vm.connect_uri = migration_obj.src_uri
            vm.start()
            vm.wait_for_login().close()
        new_hard_limit = libvirt_memory.get_qemu_process_memlock_hard_limit()
        test.log.debug("old hard limit: %s", old_hard_limit[0])
        test.log.debug("new hard limit: %s", new_hard_limit)
        if new_hard_limit != old_hard_limit[0]:
            test.fail("Check qemu process memlock hard limit failed.")
        migration_obj.run_migration_again()

    libvirt_version.is_libvirt_feature_supported(params)

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")
    service_name = params.get("service_name")

    if service_name and service_name == "virtproxyd":
        if not utils_split_daemons.is_modular_daemon():
            test.cancel("This libvirt version doesn't support virtproxyd.")

    old_hard_limit = []

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection
    migration_test_again = eval("migration_%s_again" % test_case) if "migration_%s_again" % test_case in \
        locals() else migration_obj.run_migration_again

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
        if migrate_again:
            migration_test_again()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
