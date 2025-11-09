# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from virttest import utils_net, virsh
from virttest.utils_libvirt import libvirt_vmxml
from virttest.libvirt_xml.vm_xml import VMXML

from provider.guest_os_booting import guest_os_booting_base as guest_os
from provider.migration import base_steps
from provider.virtual_network import network_base


def run(test, params, env):
    """
    Test changing multiqueue queue numbers during migration

    1. Setup VM with multiqueue network interface
    2. Start migration in background
    3. Change queue numbers repeatedly during migration
    4. Verify network connectivity and queue configuration after migration
    5. Optionally migrate back and repeat verification

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def get_queues_status(session, ifname, timeout=240):
        """
        Get current and maximum queue numbers for a network interface

        :param session: VM session object
        :param ifname: Interface name
        :param timeout: Command timeout
        :return: [max_queues, current_queues]
        """
        mq_get_cmd = "ethtool -l %s" % ifname
        nic_mq_info = session.cmd_output(mq_get_cmd, timeout=timeout)
        queues_reg = re.compile(r"Combined:\s+(\d+)", re.I)
        queues_info = queues_reg.findall(" ".join(nic_mq_info.splitlines()))
        if len(queues_info) != 2:
            err_msg = "Failed to get guest queues info, "
            err_msg += "make sure your guest supports multiqueue.\n"
            err_msg += "Check cmd is: '%s', " % mq_get_cmd
            err_msg += "Command output is: '%s'." % nic_mq_info
            test.cancel(err_msg)
        return [int(x) for x in queues_info]

    def change_queues_number(session, ifname, q_number, queues_status=None):
        """
        Change the number of combined queues for a network interface

        :param session: VM session object
        :param ifname: Interface name
        :param q_number: Target queue number
        :param queues_status: Current queue status [max, current]
        :return: Updated queue status [max, current]
        """
        if not queues_status:
            queues_status = get_queues_status(session, ifname)

        mq_set_cmd = "ethtool -L %s combined %d" % (ifname, q_number)
        status, output = session.cmd_status_output(mq_set_cmd)
        cur_queues_status = get_queues_status(session, ifname)

        err_msg = ""
        expect_q_number = q_number

        if (q_number != queues_status[1] and
                q_number <= queues_status[0] and
                q_number > 0):
            if (cur_queues_status[1] != q_number or
                    cur_queues_status[0] != queues_status[0]):
                err_msg = "Parameter is valid, but change queues failed, "
        elif cur_queues_status != queues_status:
            if q_number != queues_status[1]:
                err_msg = "Parameter is invalid, "
            err_msg += "Current queues value is not expected, "
            expect_q_number = queues_status[1]

        if len(err_msg) > 0:
            err_msg += "current queues set is %s, " % cur_queues_status[1]
            err_msg += "max allowed queues set is %s, " % cur_queues_status[0]
            err_msg += "when run cmd: '%s', " % mq_set_cmd
            err_msg += "expect queues are %s, " % expect_q_number
            err_msg += "expect max allowed queues are %s, " % queues_status[0]
            err_msg += "output: '%s'" % output
            test.fail(err_msg)

        return [int(_) for _ in cur_queues_status]

    def enable_multiqueue_in_guest(session):
        """
        Enable multiqueue in guest by setting queues to maximum

        :param session: VM session object
        """
        test.log.info("Enabling multiqueue in guest")

        # Get MAC address from VM XML and find interface name
        vm_xml = VMXML.new_from_dumpxml(vm_name)
        interfaces = vm_xml.devices.by_device_tag('interface')
        if not interfaces:
            test.fail("No network interfaces found in VM XML")

        # Use the first interface's MAC address
        mac_address = interfaces[0].mac_address
        test.log.debug("Found MAC address: %s", mac_address)

        # Get interface name using utils_net function
        ifname = utils_net.get_linux_ifname(session, mac_address)
        test.log.info("Found network interface: %s", ifname)

        # Set queues to the configured value
        queues_status = get_queues_status(session, ifname)
        target_queues = min(initial_queues, queues_status[0])
        change_queues_number(session, ifname, target_queues, queues_status)

        return ifname

    def setup_test():
        """
        Setup test environment
        """
        test.log.info("Setting up test environment")
        vm_xml_obj = VMXML.new_from_inactive_dumpxml(vm_name)
        libvirt_vmxml.modify_vm_device(vm_xml_obj, 'interface', iface_dict)

    def run_test():
        """
        Run the main test: migration with queue changes using action_during_mig
        """
        session = None
        try:
            # Enable multiqueue and get interface name
            vm.start()
            session = vm.wait_for_serial_login(timeout=login_timeout)
            ifname = enable_multiqueue_in_guest(session)
            test.log.info("Initial interface setup complete: %s", ifname)

            test.log.info("TEST_STEP: Starting migration to target host")
            migration_obj.setup_connection()
            if not vm.is_alive():
                vm.start()
            # The queue changes will be handled automatically by action_during_mig
            # during the migration process defined in the configuration
            test.log.info("Starting migration with queue changes via action_during_mig")
            migration_obj.run_migration()

            if not cancel_migration:
                test.log.info("TEST_STEP: Verifying network on target host")

                # Switch to target host
                backup_uri, vm.connect_uri = vm.connect_uri, dest_uri
                if vm.serial_console is not None:
                    vm.cleanup_serial_console()
                vm.create_serial_console()

                # Get new session on target
                target_session = vm.wait_for_serial_login(timeout=240)
                target_session.cmd("dhclient -r; dhclient", timeout=120)

                # Test network connectivity
                test.log.info("TEST_STEP: Testing network connectivity")
                ips = {'outside_ip': outside_ip}
                network_base.ping_check(params, ips, target_session)

                # Check XML configuration on target
                virsh_obj = virsh.VirshPersistent(uri=dest_uri)
                target_xml = VMXML.new_from_dumpxml(vm_name, virsh_instance=virsh_obj)
                libvirt_vmxml.check_guest_xml_by_xpaths(target_xml, expected_xpath)

                target_session.close()

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
        bk_xml.sync()

    # Parse test parameters
    vm_name = guest_os.get_vm(params)
    vm = env.get_vm(vm_name)
    params.update({"vm": vm})
    dest_uri = params.get("virsh_migrate_desturi")
    migrate_vm_back = params.get_boolean("migrate_vm_back", False)
    cancel_migration = params.get_boolean("cancel_migration", False)
    outside_ip = params.get("outside_ip", "8.8.8.8")
    initial_queues = int(params.get("initial_queues", "4"))
    login_timeout = int(params.get("login_timeout", "360"))
    iface_dict = eval(params.get("iface_dict", "{}"))
    expected_xpath = eval(params.get("expected_xpath", "{}"))
    migration_obj = base_steps.MigrationBase(test, vm, params)
    bk_xml = VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
