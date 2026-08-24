# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright IBM
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Aniket Sahu <Aniket.Sahu1@ibm.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import ast
import logging

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt as libvirt_utils

from provider.virtual_network import network_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}

LOG = logging.getLogger('avocado.' + __name__)


def setup_ovs_bridge(bridge_name, bridge_ip, bridge_netmask):
    """
    Create OVS bridge if it doesn't exist and assign an IP address to it.

    :param bridge_name: name of the OVS bridge
    :param bridge_ip: IP address to assign to the bridge
    :param bridge_netmask: prefix length (e.g. "24")
    :return: True if the bridge was created by this call, False if it pre-existed
    """
    bridge_created = False
    if not utils_net.ovs_br_exists(bridge_name):
        utils_net.add_ovs_bridge(bridge_name)
        bridge_created = True
    utils_net.bring_up_ifname(bridge_name)
    # Ignore error if address is already assigned (idempotent re-runs)
    try:
        utils_net.set_net_if_ip(bridge_name, "%s/%s" % (bridge_ip, bridge_netmask))
    except utils_net.IfChangeAddrError:
        pass
    LOG.debug("OVS bridge %s is up with %s/%s", bridge_name, bridge_ip, bridge_netmask)
    return bridge_created


def verify_ovs_port(test, iface_mac, bridge_name, vm_name):
    """
    Verify the tap device created by libvirt for the interface is connected
    to the expected OVS bridge.

    :param test: test instance
    :param iface_mac: MAC address of the attached interface
    :param bridge_name: expected OVS bridge name
    :param vm_name: VM name
    """
    tap_name = utils_misc.wait_for(
        lambda: libvirt_utils.get_ifname_host(vm_name, iface_mac),
        timeout=10)
    if not tap_name:
        test.fail("Could not find tap device for MAC %s" % iface_mac)
    _, actual_bridge = utils_net.find_current_bridge(tap_name)
    if actual_bridge != bridge_name:
        test.fail("Tap '%s' is on bridge '%s', expected '%s'"
                  % (tap_name, actual_bridge, bridge_name))
    LOG.debug("OVS port verified: tap=%s bridge=%s", tap_name, bridge_name)
    return tap_name


def verify_guest_iface_and_ping(test, vm, params, iface_mac):
    """
    Open a serial session to the guest, assign the IP from params via
    utils_net.set_guest_ip_addr, then run ping_check if requested.

    :param test: test instance
    :param vm: VM object
    :param params: test params dict (read: guest_ip, guest_netmask, vm_ping_bridge)
    :param iface_mac: MAC address of the attached interface
    """
    guest_ip = params.get("guest_ip")
    guest_netmask = params.get("guest_netmask", "255.255.255.0")
    bridge_ip = params.get("bridge_ip")

    session = vm.wait_for_serial_login()
    try:
        utils_net.set_guest_ip_addr(session, iface_mac, guest_ip, guest_netmask)
        LOG.debug("Guest IP %s/%s configured on MAC %s", guest_ip, guest_netmask, iface_mac)

        if bridge_ip:
            ips = {'bridge_ip': bridge_ip}
            network_base.ping_check(params, ips, session, force_ipv4=True)
    finally:
        session.close()


def run(test, params, env):
    """
    Test hotplug and hot-unplug of an OVS bridge interface with optional
    connectivity verification and stress (multi-iteration) support.

    Test steps:
    1. Setup OVS bridge with IP on host
    2. Start VM without any interface
    3. Attach OVS bridge interface by attach-device
    4. Verify tap device is connected to OVS bridge
    5. (Optional) Configure guest IP and ping bridge from guest
    6. Detach interface by detach-device
    7. Verify interface removed from live XML
    8. Repeat steps 3-7 for requested number of iterations
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    iface_attrs = ast.literal_eval(params.get('iface_attrs', '{}'))
    bridge_name = iface_attrs.get('source', {}).get('bridge', 'ovsbr')
    bridge_ip = params.get('bridge_ip', '')
    bridge_netmask = params.get('bridge_netmask', '24')
    verify_guest = 'yes' == params.get('verify_guest', 'no')
    iterations = int(params.get('attach_detach_iterations', '1'))

    bridge_created = False
    try:
        test.log.debug("TEST_SETUP: Configure OVS bridge and start VM")
        bridge_created = setup_ovs_bridge(bridge_name, bridge_ip, bridge_netmask)
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

        iface = libvirt_vmxml.create_vm_device_by_type('interface', iface_attrs)

        for i in range(iterations):
            test.log.debug("TEST_STEP attach [%d/%d]: attach-device", i + 1, iterations)
            virsh.attach_device(vm_name, iface.xml, wait_for_event=True, **VIRSH_ARGS)
            test.log.debug("Live XML: %s", vm_xml.VMXML.new_from_dumpxml(vm_name))

            test.log.debug("TEST_STEP verify [%d/%d]: OVS port check", i + 1, iterations)
            verify_ovs_port(test, iface_attrs['mac_address'], bridge_name, vm_name)

            if verify_guest:
                test.log.debug("TEST_STEP guest [%d/%d]: guest IP + ping", i + 1, iterations)
                verify_guest_iface_and_ping(test, vm, params, iface_attrs['mac_address'])

            test.log.debug("TEST_STEP detach [%d/%d]: detach-device", i + 1, iterations)
            virsh.detach_device(vm_name, iface.xml, wait_for_event=True, **VIRSH_ARGS)

            if not utils_misc.wait_for(
                    lambda: not vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('interface'),
                    timeout=10):
                test.fail("Interface still present in live XML after detach (iteration %d)" % (i + 1))
            test.log.debug("TEST_STEP detach [%d/%d]: interface removed from live XML", i + 1, iterations)

    finally:
        test.log.debug("TEST_TEARDOWN: Restoring VM and host state")
        if vm.is_alive():
            virsh.destroy(vm_name, **VIRSH_ARGS)
        vmxml_backup.sync()
        if bridge_created:
            utils_net.del_ovs_bridge(bridge_name)
