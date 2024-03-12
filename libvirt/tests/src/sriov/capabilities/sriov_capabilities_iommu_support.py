from virttest.libvirt_xml import capability_xml


def run(test, params, env):
    """
    check virsh capabilities output includes iommu support element
    """
    cap_xml = capability_xml.CapabilityXML()
    if cap_xml.get_iommu().get('support') != 'yes':
        test.fail("IOMMU is disabled in capabilities: %s. "
                  % cap_xml.get_iommu())
