import re
import logging

from avocado.core import exceptions
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import network_xml

from virttest import libvirt_version


def run(test, params, env):
    """
    Test command: virsh net-list.

    The command returns list of networks.
    1.Get all parameters from configuration.
    2.Get current network's status(State, Autostart).
    3.Do some prepare works for testing.
    4.Perform virsh net-list operation.
    5.Recover network status.
    6.Confirm the result.
    """
    option = params.get("net_list_option", "")
    extra = params.get("net_list_extra", "")
    status_error = params.get("status_error", "no")
    set_status = params.get("set_status", "active")
    set_persistent = params.get("set_persistent", "persistent")
    set_autostart = params.get("set_autostart", "autostart")
    error_msg = params.get("error_msg", "")

    net_name = params.get("net_name", "net-br")
    net_xml = network_xml.NetworkXML(network_name=net_name)

    # acl polkit params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise exceptions.TestSkipError("API acl test not supported"
                                           " in current libvirt version.")

    # Record current net_state_dict
    net_backup = network_xml.NetworkXML.new_all_networks_dict()
    net_backup_state = virsh.net_state_dict()
    logging.debug("Backed up network(s): %s", net_backup_state)

    # Check the network name is not duplicated
    try:
        _ = net_backup[net_name]
    except (KeyError, AttributeError):
        pass
    else:
        raise exceptions.TestSkipError("Duplicated network name: '%s'" %
                                       net_name)
    # Default the network is persistent, active, autostart
    # Create a persistent/transient network.
    if set_persistent == "persistent":
        net_xml.define()
        logging.debug("Created persistent network")
    else:
        net_xml.create()
        logging.debug("Created transient network")

    # Prepare an active/inactive network
    # For the new defined network, it's inactive by default
    if set_status == "active" and set_persistent == "persistent":
        net_xml.start()

    # Prepare an autostart/no-autostart network
    # For the new create network, it's no-autostart by default
    if set_autostart == "autostart":
        net_xml.set_autostart(True)

    try:
        virsh_dargs = {'ignore_status': True, 'debug': True}
        if params.get('setup_libvirt_polkit') == 'yes':
            virsh_dargs['unprivileged_user'] = unprivileged_user
            virsh_dargs['uri'] = uri
        ret = virsh.net_list(option, extra, **virsh_dargs)
        output = ret.stdout.strip()

        # Check test results
        if status_error == "yes":
            # Check the results with error parameter
            if error_msg:
                libvirt.check_result(ret, error_msg)
            # Check the results with correct option but inconsistent network status
            else:
                libvirt.check_exit_status(ret)
                if re.search(net_name, output):
                    raise exceptions.TestFail("virsh net-list %s get wrong results" %
                                              option)

        # Check the results with correct option and consistent network status
        else:
            libvirt.check_exit_status(ret)
            if option == "--uuid":
                uuid = virsh.net_uuid(net_name).stdout.strip()
                if not re.search(uuid, output):
                    raise exceptions.TestFail("Failed to find network: '%s' with:"
                                              "virsh net-list '%s'." % (net_name, option))
            else:
                if not re.search(net_name, output):
                    raise exceptions.TestFail("Failed to find network: '%s' with:"
                                              "virsh net-list '%s'." % (net_name, option))
    finally:
        # Recover network
        try:
            if set_status == "active":
                net_xml.del_active()
            if set_persistent == "persistent":
                net_xml.del_defined()
        except Exception:
            virsh.net_undefine()
