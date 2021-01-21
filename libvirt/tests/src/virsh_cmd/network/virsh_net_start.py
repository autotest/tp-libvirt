import logging

from virttest import virsh
from virttest import libvirt_vm
from virttest.libvirt_xml import network_xml, IPXML
from virttest import libvirt_version
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh net-start.
    """
    # Gather test parameters
    uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                      "default"))
    status_error = "yes" == params.get("status_error", "no")
    inactive_default = "yes" == params.get("net_start_inactive_default", "yes")
    net_ref = params.get("net_start_net_ref", "netname")  # default is tested
    extra = params.get("net_start_options_extra", "")  # extra cmd-line params.
    route_test = "yes" == params.get("route_test", "no")

    # make easy to maintain
    virsh_dargs = {'uri': uri, 'debug': True, 'ignore_status': True}
    virsh_instance = virsh.VirshPersistent(**virsh_dargs)

    # libvirt acl polkit related params
    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    virsh_uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    # Get all network instance
    origin_nets = network_xml.NetworkXML.new_all_networks_dict(virsh_instance)

    # Prepare default network for following test.
    try:
        default_netxml = origin_nets['default']
    except KeyError:
        virsh_instance.close_session()
        test.cancel("Test requires default network to exist")
    try:
        # To confirm default network is active
        if not default_netxml.active:
            default_netxml.active = True

        # inactive default according test's need
        if inactive_default:
            logging.info("Stopped default network")
            default_netxml.active = False

        # State before run command
        origin_state = virsh_instance.net_state_dict()
        logging.debug("Origin network(s) state: %s", origin_state)

        if route_test:
            # define a network "def" with route address is "192.168.122.x"
            # 1. start def first then start default
            current_state = virsh_instance.net_state_dict()
            if 'def' in current_state:
                virsh.net_destroy("def", ignore_status=True)
                virsh.net_undefine("def", ignore_status=True)
            expect_fail = "yes" == params.get("expect_start_fail", "no")
            test_xml = network_xml.NetworkXML(network_name="def")
            test_xml.forward = {'mode': 'nat'}
            test_xml.routes = [{'address': '192.168.122.0', 'prefix': '24', 'gateway': '192.168.100.1'}]
            ipxml = IPXML(address='192.168.100.1', netmask='255.255.255.0')
            range_4 = network_xml.RangeXML()
            range_4.attrs = {'start': '192.168.100.2', 'end': '192.168.100.254'}
            ipxml.dhcp_ranges = range_4
            test_xml.ip = ipxml
            test_xml.define()
            virsh.net_start("def")
            # start default, should fail
            result = virsh.net_start("default")
            logging.debug(result)
            libvirt.check_exit_status(result, expect_error=expect_fail)

            # 2. start default then start def
            virsh.net_destroy("def")
            virsh.net_start("default")
            current_state11 = virsh_instance.net_state_dict()
            logging.debug("before start 2nd network(s) state: %s", current_state11)
            # start def, should fail
            result = virsh.net_start("def")
            logging.debug(result)
            libvirt.check_exit_status(result, expect_error=expect_fail)
            current_state12 = virsh_instance.net_state_dict()
            logging.debug("after start 2nd network(s) state: %s", current_state12)
            # clear the env
            virsh.net_undefine("def")

        else:
            if net_ref == "netname":
                net_ref = default_netxml.name
            elif net_ref == "netuuid":
                net_ref = default_netxml.uuid

            if params.get('setup_libvirt_polkit') == 'yes':
                virsh_dargs = {'uri': virsh_uri, 'unprivileged_user': unprivileged_user,
                               'debug': False, 'ignore_status': True}
            if params.get('net_start_readonly', 'no') == 'yes':
                virsh_dargs = {'uri': uri, 'debug': True, 'readonly': True, 'ignore_status': True}

        # Run test case
        if 'unprivileged_user' in virsh_dargs and status_error:
            test_virsh = virsh.VirshPersistent(unprivileged_user=virsh_dargs['unprivileged_user'])
            virsh_dargs.pop('unprivileged_user')
            result = test_virsh.net_start(net_ref, extra, **virsh_dargs)
            test_virsh.close_session()
        elif not route_test:
            result = virsh.net_start(net_ref, extra, **virsh_dargs)
        logging.debug(result)
        status = result.exit_status

        # Get current net_stat_dict
        current_state = virsh_instance.net_state_dict()
        logging.debug("Current network(s) state: %s", current_state)
        if 'default' not in current_state:
            test.fail('Network "default" cannot be found')
        # Check status_error
        if status_error:
            if not status:
                test.fail("Run successfully with wrong command!")
        else:
            if status:
                test.fail("Run failed with right command")

            # Get current net_stat_dict
            current_state = virsh_instance.net_state_dict()
            logging.debug("Current network(s) state: %s", current_state)
            is_default_active = current_state['default']['active']
            if not is_default_active:
                test.fail("Execute cmd successfully but default is inactive actually.")
    finally:
        virsh_instance.close_session()
        current_state = virsh_instance.net_state_dict()
        if "def" in current_state:
            virsh.net_destroy("def", ignore_status=True)
            virsh.net_undefine("def", ignore_status=True)
        virsh.net_start('default', debug=True, ignore_status=True)
