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
from virttest import utils_net
from virttest.libvirt_xml import vm_xml
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
    Main entry point for hotplugging.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    ping_params = {'vm_ping_host': 'pass'}
    ips = {'host_ip': utils_net.get_host_ip_address(params)}
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    iface = libvirt.get_vm_device(vmxml, "interface", index=-1)[0]
    operation_method = params.get("operation_method")

    def setup_test():
        """
        Setup test environment for hotplugging test.
        """
        test.log.info("TEST_SETUP: Prepare guest with non interface")
        vmxml.remove_all_device_by_type('interface')
        vmxml.sync()

    def run_test():
        """
        Execute the test steps.

        This test is adapted from tp-qemu pci_hotplug.hotplug_nic.nic_virtio_net.with_shutdown
        to specifically validate virtio-net device hotplug/unplug functionality followed by
        guest shutdown in libvirt environment.

        Test scenario:
        1. Start VM without interface.
        2. Hotplug virtio-net interface to running VM
        3. Verify interface appears.
        4. Test network connectivity
        5. Execute guest operation.
        """
        test.log.info("TEST_STEP 1: Attaching interface")
        if not vm.is_alive():
            vm.start()
        virsh.attach_device(vm_name, iface.xml, wait_for_event=True, flagstr='--live', **virsh_opt)
        test.log.debug("After attaching interface, vm xml:%s ", vm_xml.VMXML.new_from_dumpxml(vm_name))

        test.log.info("TEST_STEP 2: Verify PCI device")
        session = vm.wait_for_login()
        if not utils_misc.wait_for(lambda: verify_pci_device_attached(test, session, params), 30, 3):
            test.fail("Failed to verify PCI device using match_string '%s'" % params.get("match_string"))

        test.log.info("TEST_STEP 3: Test network connectivity")
        network_base.ping_check(ping_params, ips, session, force_ipv4=True)

        test.log.info("TEST_STEP 4: Do %s in guest" % operation_method)
        session.sendline(operation_method)
        if not utils_misc.wait_for(lambda: vm.state() == params.get("expected_state"), 300):
            test.fail("VM failed to %s within timeout" % operation_method)
        test.log.debug("Check vm state PASS")

    setup_test()
    run_test()
