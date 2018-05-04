import re
import logging

from avocado.utils import process
from virttest import virsh
from provider import libvirt_version


# if the net is transisent, dump the xml then define it to make it persistent
def make_net_persistent(net_name):
    logging.debug(virsh.net_dumpxml(net_name).stdout)
    with open("/tmp/default.xml", "w") as f:
        f.write(virsh.net_dumpxml(net_name).stdout)
    virsh.net_define("/tmp/default.xml", ignore_status=False)
    return None


def run(test, params, env):
    """
    Test command: virsh net-destroy.

    The command can forcefully stop a given network.
    1.Make sure the network exists.
    2.Prepare network status.
    3.Perform virsh net-destroy operation.
    4.Check if the network has been destroied.
    5.Recover network environment.
    6.Confirm the test result.
    """

    net_ref = params.get("net_destroy_net_ref")
    extra = params.get("net_destroy_extra", "")
    network_name = params.get("net_destroy_network", "default")
    network_status = params.get("net_destroy_status", "active")
    status_error = params.get("status_error", "no")
    net_persistent = "yes" == params.get("net_persistent", "yes")
    net_cfg_file = params.get("net_cfg_file", "/usr/share/libvirt/networks/default.xml")

    # libvirt acl polkit related params
    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    output_all = virsh.net_list("--all").stdout.strip()
    # prepare the network status: active, persistent
    if not re.search(network_name, output_all):
        if net_persistent:
            virsh.net_define(net_cfg_file, ignore_status=False)
            virsh.net_start(network_name, ignore_status=False)
        else:
            virsh.create(net_cfg_file, ignore_status=False)
    if net_persistent:
        if not virsh.net_state_dict()[network_name]['persistent']:
            logging.debug("!!!make the network persistent")
            make_net_persistent(network_name)
    else:
        if virsh.net_state_dict()[network_name]['persistent']:
            virsh.net_undefine(network_name, ignore_status=False)
    if not virsh.net_state_dict()[network_name]['active']:
        if network_status == "active":
            virsh.net_start(network_name, ignore_status=False)
    else:
        if network_status == "inactive":
            logging.debug("!!!destroy the network as we need to test inactive")
            virsh.net_destroy(network_name, ignore_status=False)
    logging.debug("After prepare: %s" % virsh.net_state_dict())

    # Run test case
    if net_ref == "uuid":
        net_ref = virsh.net_uuid(network_name).stdout.strip()
    elif net_ref == "name":
        net_ref = network_name

    status = virsh.net_destroy(net_ref, extra, uri=uri, debug=True,
                               unprivileged_user=unprivileged_user,
                               ignore_status=True).exit_status

    # Confirm the network has been destroied.
    if net_persistent:
        if virsh.net_state_dict()[network_name]['active']:
            status = 1
    else:
        output_all = virsh.net_list("--all").stdout.strip()
        if re.search(network_name, output_all):
            status = 1
            logging.debug("transient network should not exists after destroy")

    # Recover network status to system default status
    try:
        if network_name not in virsh.net_state_dict():
            virsh.net_define(net_cfg_file, ignore_status=False)
        if not virsh.net_state_dict()[network_name]['active']:
            virsh.net_start(network_name, ignore_status=False)
        if not virsh.net_state_dict()[network_name]['persistent']:
            make_net_persistent(network_name)
        if not virsh.net_state_dict()[network_name]['autostart']:
            virsh.net_autostart(network_name, ignore_status=False)
    except process.CmdError:
        test.error("Recover network status failed!")
    # Check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command")
    else:
        test.error("The status_error must be 'yes' or 'no'!")
