from virttest.libvirt_xml.devices.hostdev import Hostdev


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
