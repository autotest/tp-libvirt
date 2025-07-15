import logging

from virttest import utils_net
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

LOG = logging.getLogger("avocado." + __name__)


def parse_iface_dict(vf_pci, params):
    """
    Parse interface dictionary from parameters

    :param vf_pci: The pci address of VF
    :param params: The parameters of the test
    :return: The dictionary of interface
    """
    mac_addr = utils_net.generate_mac_address_simple()

    vf_pci_addr = utils_sriov.pci_to_addr(vf_pci)
    if params.get("iface_dict"):
        iface_dict = eval(params.get("iface_dict", "{}"))
    else:
        if vf_pci_addr.get("type"):
            del vf_pci_addr["type"]
        iface_dict = eval(params.get("hostdev_dict", "{}"))
    driver_dict = eval(params.get("driver_dict", "{}"))
    if driver_dict:
        iface_dict.update(driver_dict)
    LOG.debug(f"Iface dict: {iface_dict}")

    return iface_dict


def attach_dev(vm, params):
    """
    Attach device(s) to VM

    :param vm: The vm object
    :param params: The parameters of the test
    :return: The Attached devices' name
    """
    hotplug = params.get("hotplug", "no") == "yes"
    test_pf = params.get("test_pf", "ens3f0np0")
    pf_pci = utils_sriov.get_pf_pci(test_pf=test_pf)
    dev_type = params.get("dev_type", "hostdev_interface")
    device_type = "hostdev" if dev_type == "hostdev_device" else "interface"
    iface_number = int(params.get("iface_number", "1"))
    managed = params.get("managed")
    virsh_args = {"ignore_status": False, "debug": True}
    dev_names = []

    for idx in range(iface_number):
        LOG.info("Attach a hostdev interface/device to VM")
        vf_pci = utils_sriov.get_vf_pci_id(pf_pci, idx)
        dev_name = utils_sriov.get_device_name(vf_pci)
        dev_names.append(dev_name)
        if managed == "no":
            virsh.nodedev_detach(dev_name, debug=True, ignore_status=False)
        iface_dict = parse_iface_dict(vf_pci, params)

        if hotplug:
            iface_dev = libvirt_vmxml.create_vm_device_by_type(device_type, iface_dict)
            virsh.attach_device(vm.name, iface_dev.xml, **virsh_args)
        else:
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name), device_type, iface_dict, index=idx)
    return dev_names
