import logging as log
import time

from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_net
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


logging = log.getLogger('avocado.' + __name__)


def set_interface_options(iface_type=None, iface_source=None, iface_mac=None,
                          suffix="", operation_type="attach", iface_model=None):
    """
    Set attach-detach-interface options.

    :param iface_type: network interface type
    :param iface_source: source of network interface
    :param iface_mac: interface mac address
    :param suffix: attach/detach interface options
    :param operation_type: attach or detach
    :param iface_model: interface model type
    """
    options = ""
    if iface_type is not None:
        options += " --type '{}'".format(iface_type)
    if iface_source is not None and operation_type == "attach":
        options += " --source '{}'".format(iface_source)
    if iface_mac is not None:
        options += " --mac '{}'".format(iface_mac)
    if iface_model is not None:
        options += " --model '{}'".format(iface_model)
    if suffix:
        options += " {}".format(suffix)
    return options


def check_interface_in_xml(vm_name, iface_mac, is_active=True):
    """
    Check if interface with given MAC exists in VM XML.

    :param vm_name: VM name
    :param iface_mac: interface MAC address
    :param is_active: check active or inactive XML
    :return: True if interface exists, False otherwise
    """
    if is_active:
        dumped_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    else:
        dumped_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    ifaces = dumped_vmxml.devices.by_device_tag('interface')
    for iface in ifaces:
        if iface.mac_address == iface_mac:
            logging.debug("Found interface with MAC %s in %s XML",
                          iface_mac, "active" if is_active else "inactive")
            return True

    logging.debug("Interface with MAC %s not found in %s XML",
                  iface_mac, "active" if is_active else "inactive")
    return False


def get_interface_pci_address(vm_name, iface_mac, is_active=True):
    """
    Get PCI address of interface with given MAC from VM XML.

    :param vm_name: VM name
    :param iface_mac: interface MAC address
    :param is_active: check active or inactive XML
    :return: PCI address string or None if not found
    """
    if is_active:
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
                              iface_mac, "active" if is_active else "inactive", pci_addr)
                return pci_addr

    logging.debug("Interface %s PCI address not found in %s XML",
                  iface_mac, "active" if is_active else "inactive")
    return None


def run(test, params, env):
    """
    Test detach-interface with --persistent when live xml and inactive xml
    have different PCI address for interface.

    Test steps:
    1. Start a VM, then hotplug 3 interfaces by attach-interface with virtio,
       all interfaces attached successfully.
    2. Hotplug an interface with --persistent and --mac options,
       interface attached successfully.
    3. Check the PCI address is different in the active and inactive xml
       for the last interface.
    4. Detach the newly added interface which has different PCI address
       between active and inactive xml with "--persistent" and --mac options,
       check interface detached successfully.
    5. Check the VM active and inactive xml, the interface does not exist.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Test parameters
    uri = libvirt_vm.normalize_connect_uri(
        params.get("connect_uri", "default"))
    initial_interface_count = int(
        params.get("initial_interface_count", "3"))

    # Interface specific attributes
    iface_type = params.get("at_detach_iface_type", "network")
    iface_source = params.get("at_detach_iface_source", "default")
    iface_model = params.get("at_detach_iface_model", "virtio")

    virsh_dargs = {'ignore_status': False, 'debug': True, 'uri': uri}

    # List to store MAC addresses of attached interfaces
    attached_macs = []

    try:
        # Step 1: Start VM and attach 3 initial interfaces
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        # Attach initial interfaces
        for i in range(initial_interface_count):
            mac = utils_net.generate_mac_address_simple()
            options = set_interface_options(iface_type, iface_source, mac,
                                            "", "attach", iface_model)
            attach_result = virsh.attach_interface(
                vm_name, options, **virsh_dargs)
            libvirt.check_exit_status(attach_result) 

            attached_macs.append(mac)
            logging.info(
                "Successfully attached interface %d with MAC %s", i + 1, mac)

        # Step 2: Attach interface with --persistent and --mac options
        persistent_mac = utils_net.generate_mac_address_simple()
        persistent_options = set_interface_options(iface_type, iface_source, persistent_mac,
                                                   "--persistent", "attach", iface_model)
        attach_result = virsh.attach_interface(
            vm_name, persistent_options, **virsh_dargs)
        libvirt.check_exit_status(attach_result)

        attached_macs.append(persistent_mac)
        logging.info(
            "Successfully attached persistent interface with MAC %s", persistent_mac)

        # Step 3: Check PCI address difference between active and inactive XML
        active_pci = get_interface_pci_address(
            vm_name, persistent_mac, True)
        inactive_pci = get_interface_pci_address(
            vm_name, persistent_mac, False)
        if active_pci is None or inactive_pci is None:
            xml_type = "active" if active_pci is None else "inactive"
            test.fail(
                f"Could not find PCI address for interface {persistent_mac} in {xml_type} XML")
        if active_pci == inactive_pci:
            test.fail(
                f"PCI addresses are the same in active and inactive XML: {active_pci}")
        else:
            logging.info("PCI addresses differ between active (%s) and inactive (%s) XML as expected",
                         active_pci, inactive_pci)

        # Step 4: Detach the persistent interface with --persistent option
        detach_options = set_interface_options(iface_type, None, persistent_mac,
                                               "--persistent", "detach", None)
        detach_result = virsh.detach_interface(
            vm_name, detach_options, **virsh_dargs)
        libvirt.check_exit_status(detach_result)
        logging.info(
            "Successfully detached persistent interface with MAC %s", persistent_mac)

        # Step 5: Verify interface no longer exists in both active and inactive XML
        if check_interface_in_xml(vm_name, persistent_mac, True):
            test.fail(
                f"Interface {persistent_mac} still exists in active XML after detach")
        if check_interface_in_xml(vm_name, persistent_mac, False):
            test.fail(
                f"Interface {persistent_mac} still exists in inactive XML after detach")

        # Remove the persistent MAC from our tracking list since it was detached
        attached_macs.remove(persistent_mac)

    finally:
        # Restore original VM configuration
        if vm.is_alive():
            vm.destroy()
        backup_xml.sync()
