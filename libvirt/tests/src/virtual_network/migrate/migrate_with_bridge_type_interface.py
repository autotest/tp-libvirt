# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import aexpect.remote
import re
import os

from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.libvirt_xml.vm_xml import VMXML

from provider.guest_os_booting import guest_os_booting_base as guest_os
from provider.migration import base_steps
from provider.virtual_network import network_base


def run(test, params, env):
    """
    Test migration with bridge type interface
    1. Setup bridge and virtual network according to bridge type
    2. Migrate to target host
    3. Check on target host for network functions:
       - Guest ping outside
       - Check for multiqueue
       - Check multiqueues in VM live XML (should have <driver ... queues='5'>)
    4. Migrate back from dst to src

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def check_multiqueue_in_guest(vm_session):
        """
        Check multiqueue configuration inside the guest.

        :params, vm_session: vm session object.
        """
        test.log.info("Checking multiqueue configuration in guest")
        guest_iface_info = vm_session.cmd_output("ip --color=never l").strip()
        iface_matches = re.findall(
            r"^\d+: (\S+?)[@:].*state UP.*$", guest_iface_info, re.MULTILINE)
        if not iface_matches:
            test.fail("Failed to get network interface name in guest")
        iface_name = iface_matches[0]

        _, output = vm_session.cmd_status_output("ethtool -l %s" % iface_name)
        test.log.debug("ethtool cmd output:%s" % output)
        if not re.findall("Combined:.*?%s" % iface_queues, output):
            test.fail("Expected Current hardware settings Combined: %d" % iface_queues)

        test.log.info("Setting combined queues to 3 for %s", iface_name)
        utils_net.set_channel(vm_session, iface_name, "combined", new_queues)
        _, output = vm_session.cmd_status_output("ethtool -l %s" % iface_name)
        if not re.findall("Combined:.*?%s" % new_queues, output):
            test.fail("Failed to set combined queues: %s" % new_queues)

    def setup_vm_interface():
        """
        Setup VM interface according to configuration
        """
        vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
        vm_xml.remove_all_device_by_type('interface')
        vm_xml.sync()
        if interface_timing == "hotplug":
            if not vm.is_alive():
                vm.start()
            iface = libvirt_vmxml.create_vm_device_by_type('interface', iface_dict)
            virsh.attach_device(vm_name, iface.xml, flagstr="--config",
                                debug=True, ignore_status=False)
        else:
            libvirt_vmxml.modify_vm_device(
                VMXML.new_from_inactive_dumpxml(vm_name), 'interface',
                iface_dict)
            vm.start()
        vm.wait_for_serial_login().close()
        test.log.debug("Guest xml:\n%s", VMXML.new_from_dumpxml(vm_name))

    def cleanup_nvram():
        """
        Clean up NVRAM file to avoid UEFI boot issues
        """
        nvram_file = f"/var/lib/libvirt/qemu/nvram/{vm_name}_VARS.fd"
        if os.path.exists(nvram_file):
            test.log.info(f"Removing NVRAM file: {nvram_file}")
            os.remove(nvram_file)

    def setup_test():
        """
        Setup test environment for migration with bridge type interface
        """
        test.log.info("Setting up test environment")
        # Clean up NVRAM to avoid UEFI boot issues
        cleanup_nvram()

        if bridge_type == "linux":
            utils_net.create_linux_bridge_tmux(bridge_name, iface_name=host_iface)

            remote_host_iface = utils_net.get_default_gateway(
                iface_name=True, session=remote_session, force_dhcp=True, json=True)
            params.update({"remote_host_iface": remote_host_iface})
            utils_net.create_linux_bridge_tmux(
                bridge_name, iface_name=remote_host_iface, session=remote_session)

        elif bridge_type == "ovs":
            utils_net.create_ovs_bridge(ovs_bridge_name, ip_options='-color=never')

            utils_net.create_ovs_bridge(ovs_bridge_name, session=remote_session,
                                        ip_options='-color=never')
            libvirt_network.create_or_del_network(network_dict, remote_args=remote_virsh_dargs)
            libvirt_network.create_or_del_network(network_dict)

        if vm.is_alive():
            vm.destroy()

        setup_vm_interface()

    def run_test():
        """
        Run the main test: migration and verification
        """
        test.log.info("TEST_STEP: Migrating VM to target host")
        migration_obj.setup_connection()
        if not vm.is_alive():
            vm.start()
        migration_obj.run_migration()

        if not cancel_migration:
            test.log.info("TEST_STEP: Checking VM network connectivity on target host")
            backup_uri, vm.connect_uri = vm.connect_uri, dest_uri
            if vm.serial_console is not None:
                vm.cleanup_serial_console()
            vm.create_serial_console()
            vm_session_after_mig = vm.wait_for_serial_login(timeout=240)
            vm_session_after_mig.cmd("dhclient -r; dhclient", timeout=120)

            test.log.info("TEST_STEP: Testing guest ping to outside")
            ips = {'outside_ip': outside_ip}
            network_base.ping_check(params, ips, vm_session_after_mig)

            test.log.info("TEST_STEP: Checking multiqueue")
            check_multiqueue_in_guest(vm_session_after_mig)
            virsh_obj = virsh.VirshPersistent(uri=dest_uri)
            libvirt_vmxml.check_guest_xml_by_xpaths(
                VMXML.new_from_dumpxml(vm_name, virsh_instance=virsh_obj),
                expected_xpath)

        if migrate_vm_back:
            test.log.info("TEST_STEP: Migrating VM back to source host")
            migration_obj.run_migration_back()

    def teardown_test():
        """
        Cleanup test environment
        """
        test.log.info("Cleaning up test environment")
        if bridge_type == "linux":
            utils_net.delete_linux_bridge_tmux(bridge_name, iface_name=host_iface)
            utils_net.delete_linux_bridge_tmux(
                bridge_name, iface_name=params.get("remote_host_iface"),
                session=remote_session)

        elif bridge_type == "ovs":
            utils_net.delete_ovs_bridge(ovs_bridge_name, ip_options='-color=never')
            utils_net.delete_ovs_bridge(ovs_bridge_name, session=remote_session, ip_options='-color=never')

            libvirt_network.create_or_del_network(network_dict, is_del=True, remote_args=remote_virsh_dargs)
            libvirt_network.create_or_del_network(network_dict, is_del=True)
        migration_obj.cleanup_connection()

    server_ip = params["server_ip"] = params.get("migrate_dest_host")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd") or params.get("migrate_dest_pwd")
    outside_ip = params.get("outside_ip")
    bridge_name = params.get("bridge_name")
    bridge_type = params.get("bridge_type")
    ovs_bridge_name = params.get("ovs_bridge_name")
    network_dict = eval(params.get("network_dict", "{}"))
    interface_timing = params.get("interface_timing")
    iface_queues = int(params.get("iface_queues", "5"))
    new_queues = int(params.get("new_queues", "3"))
    migrate_vm_back = params.get_boolean("migrate_vm_back")
    cancel_migration = params.get_boolean("cancel_migration")

    remote_virsh_dargs = {'remote_ip': server_ip, 'remote_user': server_user,
                          'remote_pwd': server_pwd, 'unprivileged_user': params.get("unprivileged_user"),
                          'ssh_remote_auth': params.get("ssh_remote_auth")}

    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")
    expected_xpath = eval(params.get("expected_xpath"))
    iface_dict = eval(params.get("iface_dict", "{}"))
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True, json=True)

    vm_name = guest_os.get_vm(params)
    vm = env.get_vm(vm_name)
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    remote_session = aexpect.remote.remote_login("ssh", server_ip, "22",
                                                 server_user, server_pwd,
                                                 r'[$#%]')
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
        if remote_session:
            remote_session.close()
