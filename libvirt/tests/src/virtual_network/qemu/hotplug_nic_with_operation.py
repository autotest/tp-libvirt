# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

virsh_opt = {"debug": True, "ignore_status": False}


def verify_pci_device_attached(test, session, params):
    """
    Verify that the PCI device is properly attached.

    :param test: test object
    :param session: VM session object
    :param params: test parameters
    :return: True if PCI device is attached and matches expected string, False otherwise
    """
    find_pci_cmd = params.get("find_pci_cmd", "lspci")
    match_string = params.get("match_string", "Virtio network device")

    current_output = session.cmd_output(find_pci_cmd)
    test.log.debug("%s output: %s ", find_pci_cmd, current_output)

    if re.search(match_string, current_output, re.I | re.M):
        test.log.info("PCI device found: match_string '%s' detected", match_string)
        return True
    else:
        return False


def run(test, params, env):
    """
    Main entry point for virtio-net hotplug with shutdown test.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    iface_attrs = eval(params.get("iface_attrs", "{}"))
    ping_params = {'host_ping_vm': 'pass'}
    ips = {}
    mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)

    def setup_test():
        """
        Setup test environment for virtio-net hotplug test.
        """
        test.log.info("TEST_SETUP: Prepare guest with non interface")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.remove_all_device_by_type('interface')
        vmxml.sync()

    def run_test():
        """
        Execute the main virtio-net hotplug with shutdown test.

        This test is adapted from tp-qemu pci_hotplug.hotplug_nic.nic_virtio_net.with_shutdown
        to specifically validate virtio-net device hotplug/unplug functionality followed by
        guest shutdown in libvirt environment.

        Test scenario:
        1. Start VM without interface.
        2. Hotplug virtio-net interface to running VM
        3. Verify interface appears in guest lspci using match_string
        4. Test network connectivity
        5. Hot-unplug the virtio-net interface
        6. Execute guest shutdown
        """
        test.log.info("TEST_STEP 1: Attaching virtio-net interface")
        if not vm.is_alive():
            vm.start()
        iface = libvirt_vmxml.create_vm_device_by_type('interface', iface_attrs)
        iface.mac_address = mac
        virsh.attach_device(vm_name, iface.xml, wait_for_event=True, flagstr='--live', **virsh_opt)

        test.log.info("TEST_STEP 2: Verify PCI device")
        session = vm.wait_for_login()
        if not utils_misc.wait_for(lambda: verify_pci_device_attached(test, session, params), 30, 3):
            test.fail("Failed to verify PCI device using match_string '%s'" % params.get("match_string"))

        test.log.info("TEST_STEP 3: Get VM IP address")
        vm_ip = network_base.get_vm_ip(session, mac)
        if not vm_ip:
            test.fail("Fail to get guest ip after plugging")

        test.log.info("TEST_STEP 4: Test network connectivity (host ping guest)")
        ips['vm_ip'] = vm_ip
        network_base.ping_check(ping_params, ips, session, force_ipv4=True)
        session.close()
        test.log.info("TEST_STEP 5: Hot-unplug the virtio-net interface")
        virsh.detach_device(vm_name, iface.xml, wait_for_event=True, **virsh_opt)

        test.log.info("TEST_STEP 6: Shutting down guest")
        virsh.destroy(vm_name)
        if not utils_misc.wait_for(
                lambda: libvirt.check_vm_state(vm_name, 'shut off'), 120, 2, 1):
            test.fail("VM failed to shutdown within timeout")

    setup_test()
    run_test()
