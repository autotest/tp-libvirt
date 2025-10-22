import logging as log

from avocado.core import exceptions

from virttest import virsh
from virttest import utils_net
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml


logging = log.getLogger('avocado.' + __name__)


def check_interface_xml(vm_name, iface_type, iface_source, iface_mac, params,
                        check_active=True):
    """
    Comprehensive XML verification for attach-interface testing.

    :param vm_name: name of domain
    :param iface_type: interface device type
    :param iface_source: interface source
    :param iface_mac: interface MAC address
    :param check_active: whether to check active or inactive XML
    :return: True if interface found, False otherwise
    """
    if check_active:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        xml_type = "active"
    else:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        xml_type = "inactive"
    logging.info("Checking %s XML for interface %s",
                 xml_type, iface_mac)
    expected_xpaths_attach = params.get("expected_xpaths_attach", '{}')
    result = libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, eval(
        expected_xpaths_attach % (iface_type, iface_source, iface_mac)), ignore_status=True)
    logging.info("Interface %s %s in %s XML", iface_mac,
                 "found" if result else "not found", xml_type)
    return result


def check_interface_exists(vm, iface_mac):
    """
    VM-level interface verification using 'ip l | grep' command.

    :param vm: VM instance
    :param iface_mac: MAC address to check
    :return: tuple (status, message, interface_name)
    """
    with vm.wait_for_login() as session:
        logging.info("Checking VM interface for MAC %s", iface_mac)
        interface_name = utils_net.get_linux_ifname(session, iface_mac)
        if not interface_name:
            logging.warning("Interface with MAC %s not found in VM", iface_mac)
            return (1, "Interface not found in VM", None)
        logging.info("Interface %s found in VM (MAC: %s)",
                     interface_name, iface_mac)
    return (0, "Interface successfully found in VM", interface_name)


def get_interface_pci_address(vm_name, iface_mac, check_active=True):
    """
    Get PCI address of interface with given MAC from VM XML.

    :param vm_name: VM name
    :param iface_mac: interface MAC address
    :param check_active: check active or inactive XML
    :return: PCI address string or None if not found
    """
    if check_active:
        dumped_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    else:
        dumped_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    ifaces = dumped_vmxml.devices.by_device_tag('interface')
    for iface in ifaces:
        if iface.mac_address == iface_mac:
            # Get PCI address from the interface
            address_elem = iface.xmltreefile.find('address')
            if address_elem is not None and address_elem.get('type') == 'pci':
                domain = address_elem.get('domain', '0x0000')
                bus = address_elem.get('bus', '0x00')
                slot = address_elem.get('slot', '0x00')
                function = address_elem.get('function', '0x0')
                pci_addr = f"{domain}:{bus}:{slot}.{function}"
                logging.debug("Interface %s PCI address in %s XML: %s",
                              iface_mac, "active" if check_active else "inactive", pci_addr)
                return pci_addr
    return None


def check_pci_address_difference(vm_name, iface_mac):
    """
    Check if the PCI addresses of the interface are different between active and inactive XML

    :param vm_name: name of vm
    :param iface_mac: MAC address of the interface
    :return: Tuple of (active_pci, inactive_pci)
    :raises: TestFail exception if PCI addresses are the same or not found
    """
    active_pci = get_interface_pci_address(vm_name, iface_mac, check_active=True)
    inactive_pci = get_interface_pci_address(vm_name, iface_mac, check_active=False)
    if not active_pci or not inactive_pci:
        raise exceptions.TestFail("Failed to get PCI address - Active: %s, Inactive: %s" %
                                  (active_pci, inactive_pci))
    elif active_pci == inactive_pci:
        raise exceptions.TestFail(
            "PCI addresses are the same in active and inactive XML: %s" % active_pci)
    else:
        logging.info("PCI address differs - Active: %s, Inactive: %s",
                     active_pci, inactive_pci)
    return active_pci, inactive_pci


def comprehensive_verification(vm_name, vm, iface_type, iface_source, iface_mac,
                               flags, vm_state, params):
    """
    Multi-layer verification including XML files and VM interface status.

    :param vm_name: name of domain
    :param vm: VM instance
    :param iface_type: interface type
    :param iface_source: interface source
    :param iface_mac: interface MAC address
    :param flags: operation flags
    :param vm_state: current VM state
    :param params: test parameters
    """
    errors = []

    logging.info("Starting comprehensive verification for flags: %s, VM state: %s",
                 flags, vm_state)

    # Check active XML only when the VM is running
    if vm_state == "running":
        expected_active_xml = "yes" == params.get(
            "expected_active_xml", "no")
        active_exists = check_interface_xml(vm_name, iface_type, iface_source,
                                            iface_mac, params, check_active=True)
        if expected_active_xml and not active_exists:
            errors.append(
                "Interface not found in active XML expected with flags: %s" % flags)
        elif not expected_active_xml and active_exists:
            errors.append(
                "Interface found in active XML but should not exist with flags: %s" % flags)
        else:
            result_msg = "found" if active_exists else "not found"
            logging.info(
                "Interface %s in active XML as expected with flags: %s" % (result_msg, flags))

    # Check inactive XML
    expected_inactive_xml = "yes" == params.get(
        "expected_inactive_xml", "no")
    inactive_exists = check_interface_xml(vm_name, iface_type, iface_source,
                                          iface_mac, params, check_active=False)
    if expected_inactive_xml and not inactive_exists:
        errors.append(
            "Interface not found in inactive XML expected with flags: %s" % flags)
    elif not expected_inactive_xml and inactive_exists:
        errors.append(
            "Interface found in inactive XML but should not exist with flags: %s" % flags)
    else:
        result_msg = "found" if inactive_exists else "not found"
        logging.info(
            "Interface %s in inactive XML as expected with flags: %s" % (result_msg, flags))

    # Check VM interface using 'ip l' command if required
    expected_vm_interface = "yes" == params.get(
        "expected_vm_interface", "no")
    if expected_vm_interface and vm_state == "running":
        status, msg, iface_name = check_interface_exists(
            vm, iface_mac)
        if status != 0:
            errors.append("VM interface check via 'ip l': %s" % msg)
        else:
            logging.info("Interface found in VM (name: %s)", iface_name)

    if errors:
        raise exceptions.TestFail(
            "verification failed: %s" % "; ".join(errors))

    logging.info("All verifications passed successfully")


def run(test, params, env):
    """
    Test virsh attach-interface command with various options for running/shutdown vm.

    Test scenarios:
    1. For VM running or VM down status
    2. attach-interface command with default network with:
       - "no option"
       - "--live"
       - "--current"
       - "--persistent"
       - "--config"
       - "--config --live"
       - "--current --live" options
    3. Check attach-interface command results,XML file and interface in VM
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Test parameters
    vm_state = params.get("vm_state", "running")
    test_flags = params.get("test_flags", "")
    status_error = "yes" == params.get("status_error", "no")
    expected_error = params.get("expected_error", "")
    test_scenario = params.get("test_scenario", "")
    login_timeout = params.get_numeric("login_timeout", 360)
    initial_interface_count = params.get_numeric("initial_interface_count", 3)

    # Interface specific attributes
    iface_type = params.get("iface_type", "network")
    iface_source = params.get("iface_source", "default")
    iface_model = params.get("iface_model", "virtio")
    iface_mac = utils_net.generate_mac_address_simple()

    virsh_dargs = {'ignore_status': True, 'debug': True}

    logging.info("Test parameters - VM: %s, State: %s, Flags: '%s'",
                 vm_name, vm_state, test_flags)

    # Back up original XML
    if vm.is_alive():
        vm.destroy(gracefully=False)
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # prepare VM state
        if vm_state == "running":
            vm.start()
            vm.wait_for_login(timeout=login_timeout).close()

        if test_scenario == "pci_diff_detach":
            for _ in range(initial_interface_count):
                attach_options = ("%s %s --model %s" % (iface_type, iface_source, iface_model))
                attach_result = virsh.attach_interface(vm_name, attach_options, **virsh_dargs)
                libvirt.check_exit_status(attach_result)

        # Prepare attach options
        attach_options = ("%s %s --model %s --mac %s %s" % (iface_type, iface_source,
                                                            iface_model, iface_mac, test_flags))
        # Execute attach-interface command
        attach_result = virsh.attach_interface(vm_name, attach_options, **virsh_dargs)
        # Check command execution result
        libvirt.check_exit_status(attach_result, status_error)

        if expected_error:
            libvirt.check_result(attach_result, expected_error)
            return

        if test_scenario == "pci_diff_detach":
            logging.info("Starting PCI address difference and detach test")
            check_pci_address_difference(vm_name, iface_mac)

            logging.info("Detaching interface with MAC %s using --persistent", iface_mac)
            detach_options = "%s --mac %s --persistent" % (iface_type, iface_mac)
            detach_result = virsh.detach_interface(vm_name, detach_options, **virsh_dargs)
            libvirt.check_exit_status(detach_result)

            logging.info("Verifying interface removal from both active and inactive XML")
            if check_interface_xml(vm_name, iface_type, iface_source,
                                   iface_mac, params, check_active=True):
                test.fail("Interface still exists in active XML after detach")
            if check_interface_xml(vm_name, iface_type, iface_source,
                                   iface_mac, params, check_active=False):
                test.fail("Interface still exists in inactive XML after detach")

        if test_scenario != "pci_diff_detach":
            comprehensive_verification(vm_name, vm, iface_type, iface_source, iface_mac,
                                       test_flags, vm_state, params)
    finally:
        # Clean up: restore original VM configuration
        if vm.is_alive():
            vm.destroy(gracefully=False, free_mac_addresses=False)
        backup_xml.sync()
