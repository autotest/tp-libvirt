from virttest.libvirt_xml.devices.hostdev import Hostdev


def get_hostdev_xml(uuid, model):
    """
    Creates a hostdev instance for a mediated device
    with given uuid.

    :param uuid: UUID of the mediated device
    :param model: mediated device type model, e.g. 'vfio-ccw'
    """

    hostdev_xml = Hostdev()
    hostdev_xml.mode = "subsystem"
    hostdev_xml.model = model
    hostdev_xml.type = "mdev"
    hostdev_xml.source = hostdev_xml.new_source(**{"uuid": uuid})
    hostdev_xml.xmltreefile.write()
    return hostdev_xml
