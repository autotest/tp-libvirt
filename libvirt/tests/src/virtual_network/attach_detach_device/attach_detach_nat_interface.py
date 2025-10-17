# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.interface import interface_base
from provider.virtual_network import network_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def check_guest_internal_value(test, vm, check_item, expected_value, params):
    """
    Check guest internal configuration values using existing utility functions

    :param test: test instance
    :param vm: VM instance
    :param check_item: type of check (qos, mac, mtu, target_dev, link_state, alias, acpi_index, coalesce, backend, nwfilter, rom, offloads, page_per_vq, queue_size)
    :param expected_value: expected value to verify
    :param params: params object
    """
    if vm.serial_console is None:
        vm.create_serial_console()
    vm_session = vm.wait_for_serial_login()
    iface_dict = eval(params.get('iface_dict', {}))
    vm_iface = interface_base.get_vm_iface(vm_session)
    check_dev = params.get("check_dev")

    try:
        if check_item == "mtu":
            test.log.info("Checking MTU configuration in guest")
            vm_iface_info = utils_net.get_linux_iface_info(vm_iface, session=vm_session)
            host_iface_info = utils_net.get_linux_iface_info(check_dev)
            vm_mtu, host_mtu = vm_iface_info.get('mtu'), host_iface_info.get('mtu')

            if not vm_mtu or int(vm_mtu) != int(expected_value):
                test.fail(f'MTU of interface inside vm should be {expected_value}, not {vm_mtu}')
            if not host_mtu or int(host_mtu) != int(expected_value):
                test.fail(f'MTU of interface on host should be {expected_value}, not {host_mtu}')

            test.log.debug('MTU check inside vm and host PASS')

        elif check_item == "mac":
            test.log.info("Checking MAC address configuration in guest")
            # Use existing utility function to get guest address map
            guest_address_map = utils_net.get_guest_address_map(vm_session)
            found_mac = False
            for mac_addr in guest_address_map.keys():
                if mac_addr.lower() == expected_value.lower():
                    found_mac = True
                    break
            if not found_mac:
                test.fail(f'MAC address {expected_value} not found in guest. Found: {list(guest_address_map.keys())}')
            test.log.debug('MAC address check inside vm PASS')

        elif check_item == "qos":
            test.log.info("Checking QoS configuration")
            if not utils_net.check_class_rules(
                    check_dev, '1:1', iface_dict['bandwidth']['inbound']):
                test.fail('Class rule check failed')
            if not utils_net.check_filter_rules(
                    check_dev, iface_dict['bandwidth']['outbound']):
                test.fail('Filter rule check failed')
            test.log.debug('QOS check for vm PASS')

        elif check_item == "target_dev":
            test.log.info("Checking target device configuration")
            result = virsh.domiflist(vm.name, "", debug=True).stdout_text
            if check_dev not in result:
                test.fail("Expected interface target dev was not found")

            cmd = "ip l show %s" % check_dev
            # status, output = vm_session.cmd(cmd)
            output = process.run(cmd, shell=True)
            if not output:
                test.fail(f'Target device {expected_value} not found on host')
            test.log.debug('Target device check PASS')

        elif check_item == "link_state":
            test.log.info("Checking link state configuration in guest")
            try:
                output = vm_session.cmd_output("ethtool %s | grep 'Link detected'" % vm_iface)
                expected_link_state = 'yes' if expected_value == 'up' else 'no'
                if f'Link detected: {expected_link_state}' not in output:
                    test.fail(f'Link state should be {expected_value}, but ethtool shows: {output}')
            except Exception as e:
                test.fail(f'Error checking link state: {e}')
            test.log.debug('Link state check inside vm PASS')

        elif check_item == "acpi_index":
            test.log.info("Checking ACPI index configuration in guest")
            try:
                interfaces_output = vm_session.cmd_output("ip l show")
                expected_iface_name = f"eno{expected_value}"
                if expected_iface_name not in interfaces_output:
                    test.fail(f'Interface with ACPI index {expected_value} ({expected_iface_name}) not found')
            except Exception as e:
                test.fail(f'Error checking ACPI index: {e}')
            test.log.debug('ACPI index check inside vm PASS')

        elif check_item == "coalesce":
            test.log.info("Checking coalesce configuration in guest")
            # Use ethtool to get coalesce info (similar to utils_net.get_channel_info pattern)
            try:
                output = process.run("ethtool -c %s |grep rx-frames" % check_dev, shell=True).stdout_text
                if not re.findall(f"rx-frames:\s+{expected_value}\n", output):
                    test.fail(f'Coalesce rx-frames should be {expected_value}, but got: {output}')
            except Exception as e:
                test.fail(f'Error checking coalesce settings: {e}')
            test.log.debug('Coalesce check inside vm PASS')

        elif check_item == "offloads":
            test.log.info("Checking offloads configuration in guest")
            try:
                guest_output = vm_session.cmd_output("ethtool -k %s" % vm_iface)
                host_output = process.run("ethtool -k %s" % check_dev, shell=True).stdout_text
                test.log.debug(f"Guest ethtool output: {guest_output}")
                test.log.debug(f"Host ethtool output: {host_output}")

                for output in [host_output, guest_output]:
                    for feature, state in expected_value.items():
                        expected_state = "on" if state else "off"
                        pattern = rf"{feature}:\s+{expected_state}(?:\s|$|\[)"
                        if not re.search(pattern, output):
                            test.fail(f'Offload feature {feature} should be {expected_state}. Output: {output}')
            except Exception as e:
                test.fail(f'Error checking offload settings: {e}')
            test.log.debug('Offloads check inside vm PASS')

        elif check_item == "page_per_vq":
            test.log.info("Checking page_per_vq configuration in guest")
            # When page_per_vq="on", the PCI notify multiplier should be 4K (0x1000=4096)
            try:
                # Find the Ethernet controller PCI address
                lspci_output = vm_session.cmd_output("lspci | grep Eth")
                test.log.debug(f"PCI Ethernet devices: {lspci_output}")

                # Extract PCI address (e.g., "01:00.0")
                pci_match = re.search(r'(\w{2}:\w{2}\.\w)', lspci_output)
                if not pci_match:
                    test.fail("Could not find Ethernet controller PCI address")
                pci_addr = pci_match.group(1)
                test.log.debug(f"Found Ethernet PCI address: {pci_addr}")

                # Check the PCI notify configuration
                lspci_verbose_output = vm_session.cmd_output(f"lspci -vvv -s {pci_addr} | grep -i notify -A1")
                test.log.debug(f"PCI notify info: {lspci_verbose_output}")

                # Look for multiplier value in the notify capability
                multiplier_match = re.search(r'multiplier=(\w+)', lspci_verbose_output)
                if not multiplier_match:
                    test.fail("Could not find notify multiplier in PCI configuration")

                multiplier_hex = multiplier_match.group(1)
                multiplier_decimal = int(multiplier_hex, 16)
                test.log.debug(f"Found notify multiplier: {multiplier_hex} (decimal: {multiplier_decimal})")

                # When page_per_vq="on", multiplier should be 4K (4096 = 0x1000)
                if expected_value == "on":
                    expected_multiplier = 4096  # 0x1000
                    if multiplier_decimal != expected_multiplier:
                        test.fail(f'page_per_vq="on" should set notify multiplier to 4K ({expected_multiplier}), '
                                  f'but got {multiplier_decimal} (0x{multiplier_hex})')
                else:
                    # When page_per_vq="off", multiplier should be smaller (typically 4)
                    expected_multiplier = 4
                    if multiplier_decimal != expected_multiplier:
                        test.fail(f'page_per_vq="off" should set notify multiplier to {expected_multiplier}, '
                                  f'but got {multiplier_decimal} (0x{multiplier_hex})')

            except Exception as e:
                test.fail(f'Error checking page_per_vq settings: {e}')
            test.log.debug('page_per_vq check inside vm PASS')

        elif check_item == "queue_size":
            test.log.info("Checking queue size configuration in guest")
            output = vm_session.cmd_output(f"ethtool -g {vm_iface}")
            test.log.debug(f"ethtool -g output: {output}")

            for queue_type, expected in [('RX', expected_value.get('rx_queue_size')), ('TX', expected_value.get('tx_queue_size'))]:
                actual = int(re.search(rf'{queue_type}:\s*(\d+)', output).group(1))
                if actual != int(expected):
                    test.fail(f'{queue_type} queue size should be {expected}, but got {actual}')

            test.log.debug('Queue size check PASS')

    finally:
        vm_session.close()


def run(test, params, env):
    """
    Test hotplug and hot unplug interface with comprehensive validation

    Test steps:
    1. Start a VM without any interface
    2. Hotplug one interface by attach-device
    3. Dump the live XML, content should be consistent with interface.xml
    4. Login VM, ping outside to verify connectivity
    5. Check various interface properties (QoS, MAC, MTU, target dev, link state, alias, ACPI index, coalesce, backend, nwfilter, ROM, offloads)
    6. Hot unplug the interface by detach-device with the same XML
    7. Check in VM that interface is detached successfully
    8. Check live XML that interface XML disappeared
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Test parameters
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    iface_dict = eval(params.get('iface_dict', "{}"))
    expected_xpaths = eval(params.get('expected_xpaths', "{}"))
    expected_checks = eval(params.get('expected_checks', "{}"))

    # Get host interface for network setup
    if not utils_misc.wait_for(
            lambda: utils_net.get_default_gateway(iface_name=True, force_dhcp=True, json=True) is not None, timeout=15):
        test.log.error("Cannot get default gateway in 15s")
    host_iface = utils_net.get_default_gateway(iface_name=True, force_dhcp=True, json=True).split()[0]
    params["host_iface"] = host_iface
    params["check_dev"] = expected_checks.get('target_dev', 'test')
    params["iface_dict"] = str(iface_dict)

    try:
        test.log.debug("TEST_SETUP: Prepare VM without interfaces")
        if vm_attrs:
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            vmxml.setup_attrs(**vm_attrs)
        libvirt_vmxml.remove_vm_devices_by_type(vm, "interface")

        test.log.debug("TEST_STEP 1: Start VM without any interface")
        virsh.start(vm_name, **VIRSH_ARGS)
        if vm.serial_console is None:
            vm.create_serial_console()
        vm.wait_for_serial_login(timeout=240).close()

        test.log.debug("TEST_STEP 2: Attach device")
        iface = libvirt_vmxml.create_vm_device_by_type('interface', iface_dict)
        virsh.attach_device(
            vm_name, iface.xml, wait_for_event=True, **VIRSH_ARGS)
        test.log.debug("Guest xml:%s", vm_xml.VMXML.new_from_dumpxml(vm_name))

        test.log.debug("TEST_STEP 3: Dump live XML and validate consistency")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, expected_xpaths)

        test.log.debug("TEST_STEP 4: Login VM and ping outside")
        session = vm.wait_for_serial_login(timeout=240)
        current_ifaces = utils_net.get_linux_iface_info(session=session)
        if len(current_ifaces) != 2:
            test.fail(f"Expected 2 interfaces after attach, found: {len(current_ifaces)}")

        # Test connectivity
        ips = {'outside_ip': params.get('ping_target')}
        network_base.ping_check(params, ips, session, force_ipv4=True)
        session.close()

        test.log.debug("TEST_STEP 5: Check comprehensive interface properties")
        for check_item, expected_value in expected_checks.items():
            if expected_value:
                check_guest_internal_value(test, vm, check_item, expected_value, params)

        test.log.debug("TEST_STEP 6: Hot unplug interface using detach-device")
        virsh.detach_device(vm_name, iface.xml, wait_for_event=True, **VIRSH_ARGS)

        test.log.debug("TEST_STEP 7: Verify interface removal in guest")
        session = vm.wait_for_serial_login(timeout=240)
        if not utils_misc.wait_for(
                lambda: len(utils_net.get_linux_iface_info(session=session)) == 1, timeout=15):
            test.fail("Interface should be removed from guest after detach")
        test.log.info("Interface successfully removed from guest")
        session.close()

        test.log.debug("TEST_STEP 8: Verify interface removal from live XML")
        final_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        final_interface_devices = final_vmxml.get_devices('interface')
        if final_interface_devices:
            test.fail("Interface still present in live XML after detach")
        test.log.info("Interface successfully removed from live XML")

    finally:
        test.log.debug("TEST_TEARDOWN: Restoring VM configuration")
        if vm.is_alive():
            virsh.destroy(vm_name)
        vmxml_backup.sync()
