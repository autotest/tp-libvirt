import re
import logging as log

from virttest import virsh
from virttest.libvirt_xml import network_xml


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test command: virsh net-uuid.

    The command can convert a network name to network UUID.
    1.Get all parameters from config file.
    2.Get uuid by network name from network xml.
    3.Perform virsh net-uuid operation.
    4.Confirm the test result.
    """
    net_ref = params.get("net_uuid_net_ref")
    net_name = params.get("net_uuid_network", "default")
    extra = params.get("net_uuid_extra", "")
    status_error = params.get("status_error", "no")

    # Confirm the network exists.
    output_all = virsh.net_list("--all").stdout.strip()
    if not re.search(net_name, output_all):
        test.cancel("Make sure the network exists!!")

    try:
        net_uuid = network_xml.NetworkXML.get_uuid_by_name(net_name)
    except IOError:
        test.error("Get network uuid failed!")

    if net_ref == "name":
        net_ref = net_name

    result = virsh.net_uuid(net_ref, extra, ignore_status=True)
    status = result.exit_status
    output = result.stdout.strip()
    err = result.stderr.strip()

    # Check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command!")
        if err == "":
            test.fail("The wrong command has no error outputted!")
        else:
            logging.info("It's an expected error")
    elif status_error == "no":
        if status != 0 or output == "":
            test.fail("Run failed with right command!")
        elif output != net_uuid.strip():
            test.fail("net-uuid cannot match!")
        else:
            logging.info("Normal test passed")
    else:
        test.error("The status_error must be 'yes' or 'no'!")
