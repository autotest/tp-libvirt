import re

from virttest import utils_misc
from virttest.libvirt_xml import capability_xml


def run(test, params, env):
    """
    check virsh capabilities output includes iommu support element
    """
    kernel_cmd = utils_misc.get_ker_cmd()
    res = re.search("iommu=on", kernel_cmd)
    if not res:
        test.error("iommu should be enabled in kernel cmd line - "
                   "'%s'." % res)
    cap_xml = capability_xml.CapabilityXML()
    if cap_xml.get_iommu().get('support') != 'yes':
        test.fail("IOMMU is disabled in capabilities: %s. "
                  % cap_xml.get_iommu())
