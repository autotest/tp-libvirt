import logging as log
import time

from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_net
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
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
    vm_ref = params.get("at_detach_iface_vm_ref", "domname")
    start_vm = params.get("start_vm", "yes")
    initial_interface_count = int(
        params.get("initial_interface_count", "3"))

    # Interface specific attributes
    iface_type = params.get("at_detach_iface_type", "network")
    iface_source = params.get("at_detach_iface_source", "default")
    iface_model = params.get("at_detach_iface_model", "virtio")

    virsh_dargs = {'ignore_status': True, 'debug': True, 'uri': uri}

    # List to store MAC addresses of attached interfaces
    attached_macs = []

    try:
        # Step 1: Start VM and attach 3 initial interfaces
        logging.info("Step 1: Starting VM and attaching %d initial interfaces",
                     initial_interface_count)

        if start_vm == "yes":
            if not vm.is_alive():
                vm.start()
            vm.wait_for_login().close()

        # Set VM reference
        if vm_ref == "domname":
            vm_ref = vm_name
        elif vm_ref == "domid":
            vm_ref = vm.get_id()
        elif vm_ref == "domuuid":
            vm_ref = vm.get_uuid()

        # Attach initial interfaces
        for i in range(initial_interface_count):
            mac = utils_net.generate_mac_address_simple()
            options = set_interface_options(iface_type, iface_source, mac,
                                            "", "attach", iface_model)

            logging.info(
                "Attaching interface %d with MAC %s", i + 1, mac)
            attach_result = virsh.attach_interface(
                vm_ref, options, **virsh_dargs)

            if attach_result.exit_status != 0:
                test.fail("Failed to attach interface %d: %s" %
                          (i + 1, attach_result.stderr))

            attached_macs.append(mac)
            logging.info(
                "Successfully attached interface %d with MAC %s", i + 1, mac)

        logging.info("Step 1 completed: All %d initial interfaces attached successfully",
                     initial_interface_count)

        # Step 2: Attach interface with --persistent and --mac options
        logging.info(
            "Step 2: Attaching interface with --persistent option")

        persistent_mac = utils_net.generate_mac_address_simple()
        persistent_options = set_interface_options(iface_type, iface_source, persistent_mac,
                                                   "--persistent", "attach", iface_model)

        logging.info("Attaching persistent interface with MAC %s",
                     persistent_mac)
        attach_result = virsh.attach_interface(
            vm_ref, persistent_options, **virsh_dargs)

        if attach_result.exit_status != 0:
            test.fail("Failed to attach persistent interface: %s" %
                      attach_result.stderr)

        attached_macs.append(persistent_mac)
        logging.info(
            "Successfully attached persistent interface with MAC %s", persistent_mac)
        logging.info(
            "Step 2 completed: Persistent interface attached successfully")

        # Step 3: Check PCI address difference between active and inactive XML
        logging.info("Step 3: Checking PCI address differences")

        # Wait a moment for XML to stabilize
        time.sleep(2)

        active_pci = get_interface_pci_address(
            vm_name, persistent_mac, True)
        inactive_pci = get_interface_pci_address(
            vm_name, persistent_mac, False)

        logging.info("Active XML PCI address: %s", active_pci)
        logging.info("Inactive XML PCI address: %s", inactive_pci)

        if active_pci is None:
            test.fail(
                "Could not find PCI address for interface %s in active XML" % persistent_mac)

        if inactive_pci is None:
            test.fail(
                "Could not find PCI address for interface %s in inactive XML" % persistent_mac)

        if active_pci == inactive_pci:
            test.fail(
                "PCI addresses are the same in active and inactive XML: %s", active_pci)
        else:
            logging.info("PCI addresses differ between active (%s) and inactive (%s) XML as expected",
                         active_pci, inactive_pci)

        logging.info("Step 3 completed: PCI address verification done")

        # Step 4: Detach the persistent interface with --persistent option
        logging.info(
            "Step 4: Detaching persistent interface with --persistent option")

        detach_options = set_interface_options(iface_type, None, persistent_mac,
                                               "--persistent", "detach", None)

        logging.info("Detaching persistent interface with MAC %s",
                     persistent_mac)
        detach_result = virsh.detach_interface(
            vm_ref, detach_options, **virsh_dargs)

        if detach_result.exit_status != 0:
            test.fail("Failed to detach persistent interface: %s" %
                      detach_result.stderr)

        logging.info(
            "Successfully detached persistent interface with MAC %s", persistent_mac)
        logging.info(
            "Step 4 completed: Persistent interface detached successfully")

        # Step 5: Verify interface no longer exists in both active and inactive XML
        logging.info(
            "Step 5: Verifying interface removal from both XMLs")

        # Wait a moment for XML to update
        time.sleep(2)

        if check_interface_in_xml(vm_name, persistent_mac, True):
            test.fail(
                "Interface %s still exists in active XML after detach" % persistent_mac)

        if check_interface_in_xml(vm_name, persistent_mac, False):
            test.fail(
                "Interface %s still exists in inactive XML after detach" % persistent_mac)

        logging.info(
            "Step 5 completed: Interface successfully removed from both active and inactive XML")

        # Remove the persistent MAC from our tracking list since it was detached
        attached_macs.remove(persistent_mac)

        logging.info(
            "Test completed successfully: All test steps passed")

    finally:
        # Cleanup: Detach any remaining attached interfaces
        logging.info("Cleanup: Detaching remaining interfaces")
        for mac in attached_macs:
            try:
                cleanup_options = set_interface_options(
                    iface_type, None, mac, "", "detach", None)
                virsh.detach_interface(
                    vm_ref, cleanup_options, **virsh_dargs)
                logging.info("Cleaned up interface with MAC %s", mac)
            except Exception as e:
                logging.warning(
                    "Failed to cleanup interface with MAC %s: %s", mac, e)

        # Restore original VM configuration
        if vm.is_alive():
            vm.destroy()
        backup_xml.sync()
        logging.info("Restored VM to original state")
