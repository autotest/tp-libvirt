import re

from virttest import virsh
from virttest import utils_net
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base
from provider.interface import interface_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}


def check_packed_virtqueue(session, expected_packed_bit, test):
    """
    Check if packed virtqueue is enabled on the VM

    :param session: VM session
    :param expected_packed_bit: Expected packed bit value
    :param test: Test instance
    """
    test.log.debug("Checking packed virtqueue configuration...")

    # 1. Get the PCI address of the network device
    lspci_output = session.cmd_output("lspci | grep Eth")
    test.log.debug(f"lspci output: {lspci_output}")

    # 2. Extract PCI address and find virtio features file
    pci_address = re.match(r'^([0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])', lspci_output.strip())
    if not pci_address:
        test.fail(f"Could not extract PCI address from lspci output: {lspci_output}")
    pci_addr = pci_address.group(1)
    test.log.debug(f"Extracted PCI address: {pci_addr}")

    find_cmd = 'find / -name features | grep "%s"' % pci_addr
    virtio_features_file = session.cmd_output(find_cmd)

    if not virtio_features_file:
        test.fail(f"Virtio features file not found for PCI address {pci_addr}")
    test.log.debug(f"Using virtio features file: {virtio_features_file}")

    packed_bit_cmd = f"cat {virtio_features_file}"
    output_lines = session.cmd_output(packed_bit_cmd).split('\n')
    test.log.debug(f"Features file output lines: {len(output_lines)}")

    # Use the second line if available, otherwise first line
    if len(output_lines) > 1 and output_lines[1].strip():
        features_line = output_lines[1]
    else:
        features_line = output_lines[0]
    test.log.debug(f"Using features line: {features_line}")

    if len(features_line) <= 34:
        test.fail(f"Features line too short: {len(features_line)} chars, need at least 35 for bit 34")

    packed_bit = features_line[34]
    test.log.debug(f"Packed bit: {packed_bit}")

    if packed_bit == expected_packed_bit:
        test.log.debug("Packed bit check: PASS")
    else:
        test.fail(f"Expected packed bit {expected_packed_bit}, but got {packed_bit}")


def check_multiqueue_settings(session, expected_queue_count, test):
    """
    Check multiqueue settings on the VM using utils_net.get_channel_info

    :param session: VM session
    :param expected_queue_count: Expected number of queues
    :param test: Test instance
    """
    test.log.debug("Checking multiqueue settings...")
    # Get the network interface name
    vm_iface = interface_base.get_vm_iface(session)
    test.log.debug(f"VM interface: {vm_iface}")

    # Use utils_net.get_channel_info to get channel information
    maximum_channels, current_channels = utils_net.get_channel_info(session, vm_iface)
    test.log.debug(f"Maximum channels: {maximum_channels}")
    test.log.debug(f"Current channels: {current_channels}")

    # Check Combined queue count
    max_combined = int(maximum_channels.get("Combined", "0"))
    current_combined = int(current_channels.get("Combined", "0"))

    if max_combined == expected_queue_count and current_combined == expected_queue_count:
        test.log.debug("Multiqueue settings check: PASS")
    else:
        test.fail(f"Expected {expected_queue_count} queues, but got max: {max_combined}, current: {current_combined}")


def run(test, params, env):
    """
    Test 'driver' element with 'packed' attribute of interface
    """

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    outside_ip = params.get('outside_ip')
    vcpu_num = params.get('vcpu_num', '4')
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    packed_check = params.get('packed_check', 'no') == 'yes'
    expected_packed_bit = params.get('expected_packed_bit', '1')
    check_multiqueue = params.get('check_multiqueue', 'no') == 'yes'
    expected_queue_count = int(params.get('expected_queue_count', '4'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        # Set CPU count
        vmxml.vcpu = int(vcpu_num)
        vmxml.placement = 'static'

        # Remove existing interfaces and add new one with packed virtqueue
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        # Start VM
        vm.start()
        virsh.domiflist(vm_name, **VIRSH_ARGS)
        iflist = libvirt.get_interface_details(vm_name)
        test.log.debug(f'iface attrs of vm: {iflist}')

        # Login to VM and run tests
        session = vm.wait_for_serial_login()

        test.log.debug("Test Step 1: Check network connectivity")
        vm_iface = interface_base.get_vm_iface(session)
        test.log.debug(f"VM interface: {vm_iface}")

        ips = {'outside_ip': outside_ip}
        network_base.ping_check(params, ips, session, force_ipv4=True)
        test.log.debug("Network connectivity test: PASS")

        test.log.debug("Test Step 2: Check packed virtqueue configuration")
        if packed_check:
            check_packed_virtqueue(session, expected_packed_bit, test)

        test.log.debug("Test Step 3: Check multiqueue settings")
        if check_multiqueue:
            check_multiqueue_settings(session, expected_queue_count, test)

        session.close()

    finally:
        test.log.debug("Clean up the env")
        bkxml.sync()
