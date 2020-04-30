from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def check_plug_to(vm_name, device_tag, bus_type='pcie-to-pci-bridge'):
    """
    Check if the nic is plugged onto pcie-to-pci-bridge

    :param vm_name: The vm to be checked
    :param device_tag: The device to be checked
    :param bus_type:  The bus type been expected to plug to
    :return True if plugged onto 'bus_type', otherwise False
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vsock = vmxml.xmltreefile.find('devices').find(device_tag)
    bus = int(eval(vsock.find('address').get('bus')))
    controllers = vmxml.get_controllers('pci')
    for controller in controllers:
        if controller.get('index') == bus:
            if controller.get('model') == bus_type:
                return True
            break
    return False


def get_free_pci_slot(vm_name):
    """
    Get a free slot for given vm

    :param: vm_name: The name of the vm to be performed
    :return: The first free slot of the bus
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    pci_bridge_index = get_pci_bridge_index(vm_name)
    pci_devices = vmxml.xmltreefile.find('devices').getchildren()
    used_slot = []
    for dev in pci_devices:
        address = dev.find('address')
        if (address is not None and
                address.get('bus') == pci_bridge_index):
            used_slot.append(address.get('slot'))
    for slot_index in range(1, 30):
        slot = "%0#4x" % slot_index
        if slot not in used_slot:
            return slot
    return None


def get_pci_bridge_index(vm_name):
    """
    Get the index of usable pci bridge, add one if there is not

    :param vm_name: The name of the vm to be performed
    :return: The index of the pci bridge
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    pci_controllers = vmxml.get_controllers('pci')
    for controller in pci_controllers:
        if controller.get('model') == 'pcie-to-pci-bridge':
            pci_bridge = controller
            break
    else:
        contr_dict = {'controller_type': 'pci',
                      'controller_model': 'pcie-to-pci-bridge'}
        pci_bridge = libvirt.create_controller_xml(contr_dict)
        libvirt.add_controller(vm_name, pci_bridge)
    return '%0#4x' % int(pci_bridge.get("index"))


def get_free_root_port(vm_name):
    """
    Get a free root port for rng device

    :param vm_name: The name of the vm to be performed
    :return: The bus index of free root port
    """
    root_ports = set()
    other_ports = set()
    used_slot = set()
    # Record the bus indexes for all pci controllers
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    pci_controllers = vmxml.get_controllers('pci')
    for controller in pci_controllers:
        if controller.get('model') == 'pcie-root-port':
            root_ports.add(controller.get('index'))
        else:
            other_ports.add(controller.get('index'))
    # Record the addresses being allocated for all pci devices
    pci_devices = vmxml.xmltreefile.find('devices').getchildren()
    for dev in pci_devices:
        address = dev.find('address')
        if address is not None:
            used_slot.add(address.get('bus'))
    # Find the bus address unused
    for bus_index in root_ports:
        bus = "%0#4x" % int(bus_index)
        if bus not in used_slot:
            return bus
    # Add a new pcie-root-port if no free one
    for index in range(1, 30):
        if index not in (root_ports | other_ports):
            contr_dict = {'controller_type': 'pci',
                          'controller_index': index,
                          'controller_model': 'pcie-root-port'}
            cntl_add = libvirt.create_controller_xml(contr_dict)
            libvirt.add_controller(vm_name, cntl_add)
            return "%0#4x" % int(index)
    return None
