# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import utils_net
from virttest.libvirt_xml.vm_xml import VMXML

from provider.migration import base_steps
from provider.virtual_network import network_base


def run(test, params, env):
    """
    Test changing multiqueue queue numbers during migration

    1. Setup VM with multiqueue network interface
    2. Start migration in background
    3. Change queue numbers repeatedly during migration
    4. Optionally migrate back and repeat verification

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def run_test():
        """
        Run the main test: migration with queue changes using action_during_mig
        """
        session = None
        try:
            vm.start()
            test.log.debug("TEST_STEP: Guest xml is: %s\n", VMXML.new_from_dumpxml(vm_name, options=" --xpath //interface"))
            session = vm.wait_for_login(timeout=login_timeout)
            max, cur = utils_net.get_channel_info(session, utils_net.get_linux_ifname(session)[0])
            if max['Combined'] != init_queues or cur['Combined'] != init_queues:
                test.fail("Expected init interface queues:%s, but got:%s\n" % (init_queues, (max, cur)))

            test.log.info("TEST_STEP: Testing network connectivity")
            ips = {'outside_ip': outside_ip}
            network_base.ping_check(params, ips, session)
            session.close()

            test.log.info("TEST_STEP: Starting migration to target host")
            migration_obj.setup_connection()
            if not vm.is_alive():
                vm.start()
            # The queue changes will be handled automatically by action_during_mig
            # during the migration process defined in the configuration
            test.log.info("TEST_STEP: Starting migration with queue changes via action_during_mig")
            migration_obj.run_migration()

            # Migrate back if requested
            if migrate_vm_back:
                test.log.info("TEST_STEP: Migrating VM back to source host")
                migration_obj.run_migration_back()

        finally:
            if session:
                session.close()

    def teardown_test():
        """
        Cleanup test environment
        """
        migration_obj.cleanup_connection()

    # Parse test parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    params.update({"vm": vm})
    migrate_vm_back = params.get_boolean("migrate_vm_back", True)
    outside_ip = params.get("outside_ip", "8.8.8.8")
    login_timeout = params.get_numeric("login_timeout", "240")
    init_queues = params.get("queues_nic1")

    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        run_test()
    finally:
        teardown_test()
