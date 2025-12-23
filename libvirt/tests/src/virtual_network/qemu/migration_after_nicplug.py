# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.virtual_network import network_base

virsh_opt = {"debug": True, "ignore_status": False}


def run(test, params, env):
    """
    Test migration after nic hotplug/hotunplug

    This test supports both nic hotplug and hotunplug scenarios followed by migration.
    The test covers:
    1) Hotplug(Hotunplug) a nic.
    2) Check connectivity.
    3) Do migration.
    4) Reboot vm.
    5) Check connectivity.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup test environment
        """
        test.log.info("TEST_SETUP: Prepare vm xml and state.")
        if case == "after_nichotplug":
            vmxml.remove_all_device_by_type("interface")
            vmxml.sync()
            vm.start()
            # Add --live --persistent to make case more stable.
            virsh.attach_device(vm_name, iface.xml, flagstr='--live  --persistent', **virsh_opt)

        elif case == "after_nichotunplug":
            virsh.detach_device(vm_name, iface.xml, flagstr='--live  --persistent', **virsh_opt)

        test.log.info("Before testing, Guest interface xml is: %s\n", vm_xml.VMXML.new_from_dumpxml(vm_name))

    def run_test():
        """
        Do migration and check network connectivity.
        """
        test.log.info("TEST_STEP 1: Starting migration")
        migration_obj.setup_connection(use_console=True)
        migration_obj.run_migration()

        test.log.info("TEST_STEP 2: Testing network connectivity on destination host")
        backup_uri = vm.connect_uri
        vm.connect_uri = dest_uri

        if case == "after_nichotplug":
            session = vm.wait_for_login(timeout=login_timeout)
            network_base.ping_check(params, ips, session)
            session.close()
            test.log.debug("Network connectivity checking is PASS after migration")

            session = vm.reboot()
            network_base.ping_check(params, ips, session)
            session.close()
            test.log.debug("Network connectivity checking is PASS after vm reboot")

        vm.connect_uri = backup_uri
        if migrate_vm_back:
            test.log.info("TEST_STEP 3: Migrating VM back to source host")
            migration_obj.run_migration_back()

    def teardown_test():
        """
        Cleanup test environment
        """
        migration_obj.cleanup_connection()
        bb.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    case = params.get("test_case")
    dest_uri = params.get("virsh_migrate_desturi")
    migrate_vm_back = params.get_boolean("migrate_vm_back", False)
    login_timeout = params.get_numeric("login_timeout", "240")
    outside_ip = params.get("outside_ip", "8.8.8.8")
    ips = {'outside_ip': outside_ip}
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    bb = vmxml.copy()
    iface = libvirt.get_vm_device(vmxml, "interface", index=-1)[0]

    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
