# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest.libvirt_xml.vm_xml import VMXML

from provider.migration import base_steps
from provider.virtual_network import network_base


def run(test, params, env):
    """
    Test migration with netperf stress

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def run_test():
        """
        Test migration with netperf stress
        """
        test.log.info("TEST_STEP 1: Guest xml is: %s\n", VMXML.new_from_dumpxml(vm_name))

        test.log.info("TEST_STEP 2: Starting migration with netperf stress")
        migration_obj.setup_connection()
        migration_obj.run_migration()

        test.log.info("TEST_STEP 3: Testing network connectivity on destination host")
        backup_uri = vm.connect_uri
        vm.connect_uri = dest_uri

        session = vm.wait_for_login(timeout=login_timeout)
        ips = {'outside_ip': outside_ip}
        network_base.ping_check(params, ips, session)
        session.close()
        vm.connect_uri = backup_uri

        if migrate_vm_back:
            test.log.info("TEST_STEP 4: Migrating VM back to source host")
            migration_obj.run_migration_back()

    def teardown_test():
        """
        Cleanup test environment
        """
        migration_obj.cleanup_connection()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    dest_uri = params.get("virsh_migrate_desturi")
    migrate_vm_back = params.get_boolean("migrate_vm_back", True)
    login_timeout = params.get_numeric("login_timeout", "240")
    outside_ip = params.get("outside_ip", "8.8.8.8")

    # Add required objects to params for action_during_mig
    params.update({"params": params, "test": test, "env": env})

    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        run_test()
    finally:
        teardown_test()
