import os

from virttest.libvirt_xml import domcapability_xml


def get_ovmf_path():
    """
    Retrieve the ovmf path in virsh domcapabilities

    :returns: str, ovmf path, otherwise None
    """
    domcapa_xml = domcapability_xml.DomCapabilityXML()
    ovmf_nodes = domcapa_xml.xmltreefile.findall('/os/loader/value')
    if not ovmf_nodes:
        return None
    return ovmf_nodes[0].text


def test_rm_ovmf_path(test):
    """
    Remove ovmf path and check domcapabilities can be updated accordingly

    Steps:
    1. Get ovmf path in virsh domcapabilities
    2. Rename the directory of ovmf path
    3. Check virsh domcapabilities output does not include ovmf path entry
    4. Recover the directory of ovmf path
    5. Check virsh domcapabilities output includes ovmf path entry again

    :param test: test object
    :raises: test.fail if not expected,
             test.cancel if not ready for test
    """
    # Get ovmf path information for virsh domcapabilities
    ovmf_path = get_ovmf_path()
    if not ovmf_path:
        test.cancel("OVMF path does not exist in domcapabilities output.")
    test.log.debug("OVMF path in domcapabilities:%s", ovmf_path)
    dir_ovmf_path = os.path.dirname(ovmf_path)
    # Remove ovmf path
    os.rename(dir_ovmf_path, dir_ovmf_path + '.bk')
    # Check virsh domcapabilities output without ovmf path
    ovmf_path = get_ovmf_path()
    if ovmf_path:
        test.fail("ovmf path should not exist in domcapabilities output")
    else:
        test.log.debug("ovmf path does not exist in domcapabilities output "
                       "as expected after ovmf directory '%s' "
                       "is removed", dir_ovmf_path)
    # Restore ovmf path
    os.rename(dir_ovmf_path + '.bk', dir_ovmf_path)
    # Check virsh domcapabilities output with ovmf path
    ovmf_path = get_ovmf_path()
    if not ovmf_path:
        test.fail("ovmf path should exist in domcapabilities output")
    else:
        test.log.debug("ovmf path does exist in domcapabilities output "
                       "as expected after ovmf directory '%s' "
                       "is recovered", dir_ovmf_path)


def run(test, params, env):
    """
    This file includes to test scenarios for checking outputs of
    virsh domcapabilities under certain configuration

    Scenario 1: Test virsh domcapabilities can change the output
                when ovmf path is removed

    """
    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    run_test(test)
