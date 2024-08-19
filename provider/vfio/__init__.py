import logging
import re

from avocado.core.exceptions import TestFail
from virttest.libvirt_xml.devices.hostdev import Hostdev
from virttest.libvirt_xml.nodedev_xml import MdevXML, NodedevXML

LOG = logging.getLogger("avcoado." + __name__)


def get_hostdev_xml(address, model=""):
    """
    Creates a hostdev instance for a mediated device
    with given uuid.

    :param address: For mediated devices, the UUID of the mediated device
                    For PCI devices, the full address element
    :param model: mediated device type model, e.g. 'vfio-ccw'
                  If omitted, treat as PCI device
    """

    hostdev_xml = Hostdev()
    hostdev_xml.mode = "subsystem"
    if "vfio" in model:
        hostdev_xml.model = model
        hostdev_xml.type = "mdev"
        hostdev_xml.source = hostdev_xml.new_source(**{"uuid": address})
    else:
        hostdev_xml.type = "pci"
        hostdev_xml.managed = "yes"
        hostdev_xml.source = hostdev_xml.new_source(**address)
    hostdev_xml.xmltreefile.write()
    return hostdev_xml


def get_nodedev_xml(device_type, parent, uuid, domains=[]):
    """
    Create and return the node device XML for Mediated Devices.

    :param device_type: //capabilities/type@id
    :param parent: //parent
    :param uuid: //capability/uuid
    :param domains: for vfio_ap-passthrough, the domains to be passed through
                    e.g. ["00.000e", "00.000f", "01.000e", "01.000f"]
    """
    device_xml = NodedevXML()
    device_xml["parent"] = parent
    mdev_xml = MdevXML()
    attributes = {
        "type_id": device_type,
        "uuid": uuid,
    }
    if domains:
        attrs = []
        cards = []
        doms = []
        for domain in domains:
            card, dom = domain.split(".")
            if card not in cards:
                attrs.append({"name": "assign_adapter", "value": "0x" + card})
                cards.append(card)
            if dom not in doms:
                attrs.append({"name": "assign_domain", "value": "0x" + dom})
                doms.append(dom)
        attributes["attrs"] = attrs
    mdev_xml.setup_attrs(**attributes)
    device_xml.set_cap(mdev_xml)
    LOG.debug("Device XML: %s", device_xml)
    return device_xml


def get_parent_device(device_name):
    """
    Gets the parent device name for the given device name
    as known by libvirt

    :param device_name: node device name
    """
    xml = NodedevXML.new_from_dumpxml(device_name)
    return xml["parent"]


def check_pci_device_present(guest_pci_id, name_part, session):
    """
    Checks if the pci device with id and given part of its name
    are present in the guest

    :param guest_pci_id: expected device id inside of the guest,
                         e.g. '0000:00:00.0'
    :param name_part: part of it's name e.g. "Mellanox Technologies"
    :param session: guest session
    :raises TestFail: if the device is not listed
    """
    s, o = session.cmd_status_output("lspci", print_func=LOG.debug)
    pattern = guest_pci_id + ".*" + name_part
    if s or not [x for x in o.split("\n") if re.search(pattern, x)]:
        raise TestFail(f"Couldn't find {guest_pci_id} with {name_part} in {o}")
