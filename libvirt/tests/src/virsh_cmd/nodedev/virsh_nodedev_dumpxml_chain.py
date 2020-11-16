import logging
import re

from virttest import virsh
from virttest.libvirt_xml.base import LibvirtXMLBase


def run(test, params, env):
    """
    Test properties of a chain of devices, starting at a given device
    going up per parent.
    """

    chain_start_device_pattern = params.get("chain_start_device_pattern")
    checks = eval(params.get("checks"))
    result = virsh.nodedev_list(ignore_status=False)
    selected_device = get_device(result.stdout_text.strip().splitlines(),
                                 chain_start_device_pattern)
    if not selected_device:
        test.error("No suitable device found for test."
                   "Pattern: %s. Available devices: %s." %
                   (chain_start_device_pattern, result.stdout))

    xml = get_nodedev_dumpxml(selected_device)
    validate_nodedev_xml(test, xml)
    for check in checks:
        for xpath, pattern in check.items():
            value = xml.xmltreefile.findtext(xpath)
            value = value if value else ""
            if not re.search(pattern, value):
                test.fail("Unexpected value on xpath '%s':"
                          " '%s' does not match '%s'" %
                          (xpath, value, pattern))
        xml = get_nodedev_dumpxml(xml.xmltreefile.findtext("parent"))
        validate_nodedev_xml(test, xml)


def validate_nodedev_xml(test, xml):
    """
    Validates the xml against nodedev schema

    :param test: avocado test instance
    :param xml: LibvirtXMLBase instance
    """
    result = xml.virt_xml_validate(xml.xml, "nodedev")
    if result.exit_status:
        test.fail("nodedev xml invalid: %s", xml.xml)


def get_nodedev_dumpxml(selected_device):
    """
    Returns LibvirtXMLBase instance holding the nodedev xml.

    :param selected_device: device identifier
    :return: LibvirtXMLBase instance from nodedev-dumpxml output
    """
    result = virsh.nodedev_dumpxml(selected_device, ignore_status=False)
    xml = LibvirtXMLBase()
    xml.xml = result.stdout_text
    logging.debug("nodedev-dumpxml for '%s': %s", selected_device,
                  xml.xmltreefile)
    return xml


def get_device(nodedev_list, pattern):
    """
    Returns a device identifier from the nodedev-list output
    based on a pattern.

    :param nodedev_list: The list of device identifiers, e.g.
        ["net_lo_00_00_00_00_00_00",
         "block_sda_333333330000007d0"]
    :param pattern: regex pattern to match at least one device
    :return: The first device identifier matching 'pattern'
    """
    for dev_id in nodedev_list:
        if re.search(pattern, dev_id):
            return dev_id
    return None
