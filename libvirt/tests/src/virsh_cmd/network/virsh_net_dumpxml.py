import os

from avocado.utils import process
from virttest import virsh
from virttest import data_dir

from virttest import libvirt_version


def run(test, params, env):
    """
    Test command: virsh net-dumpxml.

    This command can output the network information as an XML dump to stdout.
    1.Get all parameters from config file.
    2.If test case's network status is inactive, destroy it.
    3.Perform virsh net-dumpxml operation.
    4.Recover test environment(network status).
    5.Confirm the test result.
    """
    status_error = params.get("status_error", "no")
    net_ref = params.get("net_dumpxml_net_ref")
    net_name = params.get("net_dumpxml_network", "default")
    net_status = params.get("net_dumpxml_network_status", "active")
    xml_flie = params.get("net_dumpxml_xml_file", "default.xml")
    extra = params.get("net_dumpxml_extra", "")
    network_xml = os.path.join(data_dir.get_tmp_dir(), xml_flie)

    # acl polkit params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    # Run test case
    if net_ref == "uuid":
        net_ref = virsh.net_uuid(net_name).stdout.strip()
    elif net_ref == "name":
        net_ref = net_name

    net_status_current = "active"
    if not virsh.net_state_dict()[net_name]['active']:
        net_status_current = "inactive"

    if not virsh.net_state_dict()[net_name]['persistent']:
        test.error("Network is transient!")
    try:
        if net_status == "inactive" and net_status_current == "active":
            status_destroy = virsh.net_destroy(net_name,
                                               ignore_status=True).exit_status
            if status_destroy != 0:
                test.error("Network destroied failed!")

        virsh_dargs = {'ignore_status': True}
        if params.get('setup_libvirt_polkit') == 'yes':
            virsh_dargs['unprivileged_user'] = unprivileged_user
            virsh_dargs['uri'] = uri
        result = virsh.net_dumpxml(net_ref, extra, network_xml,
                                   **virsh_dargs)
        status = result.exit_status
        err = result.stderr.strip()
        xml_validate_cmd = "virt-xml-validate %s network" % network_xml
        valid_s = process.run(xml_validate_cmd, ignore_status=True, shell=True).exit_status

        # Check option valid or not.
        if extra.find("--") != -1:
            options = extra.split("--")
            for option in options:
                if option.strip() == "":
                    continue
                if not virsh.has_command_help_match("net-dumpxml",
                                                    option.strip()) and\
                   status_error == "no":
                    test.cancel("The current libvirt version"
                                " doesn't support '%s' option"
                                % option.strip())
    finally:
        # Recover network
        if net_status == "inactive" and net_status_current == "active":
            status_start = virsh.net_start(net_name,
                                           ignore_status=True).exit_status
            if status_start != 0:
                test.error("Network started failed!")

    # Check status_error
    if status_error == "yes":
        if status == 0:
            test.fail("Run successfully with wrong command!")
        if err == "":
            test.fail("The wrong command has no error outputed!")
    elif status_error == "no":
        if status != 0:
            test.fail("Run failed with right command!")
        if valid_s != 0:
            test.fail("Command output is invalid!")
    else:
        test.error("The status_error must be 'yes' or 'no'!")
