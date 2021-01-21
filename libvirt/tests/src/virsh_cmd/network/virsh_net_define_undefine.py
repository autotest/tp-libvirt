import logging

from avocado.utils import process

from virttest import virsh
from virttest import libvirt_vm
from virttest import xml_utils
from virttest import utils_libvirtd
from virttest.libvirt_xml import network_xml
from virttest.utils_test import libvirt

from virttest import libvirt_version


def get_network_xml_instance(virsh_dargs, test_xml, net_name,
                             net_uuid, bridge):
    test_netxml = network_xml.NetworkXML(
        virsh_instance=virsh.Virsh(**virsh_dargs))
    test_netxml.xml = test_xml.name

    # modify XML if called for
    if net_name is not "":
        test_netxml.name = net_name
    else:
        test_netxml.name = "default"
    if net_uuid is not "":
        test_netxml.uuid = net_uuid
    else:
        del test_netxml.uuid  # let libvirt auto-generate
    if bridge is not None:
        test_netxml.bridge = bridge

    # TODO: Test other network parameters

    logging.debug("Modified XML:")
    test_netxml.debug_xml()
    return test_netxml


def set_ip_section(testnet_xml, addr, ipv6=False, **dargs):
    ipxml = network_xml.IPXML(ipv6=ipv6)
    ipxml.address = addr
    if "netmask" in dargs:
        ipxml.netmask = dargs["netmask"]
    elif "prefix_v6" in dargs:
        ipxml.prefix = dargs["prefix_v6"]
        ipxml.family = "ipv6"
    if "dhcp_ranges_start" in dargs and dargs["dhcp_ranges_start"] is not None:
        dhcp_ranges_start = dargs["dhcp_ranges_start"]
        dhcp_ranges_end = dargs["dhcp_ranges_end"]
        ran = network_xml.RangeXML()
        ran.attrs = {"start": dhcp_ranges_start, "end": dhcp_ranges_end}
        ipxml.dhcp_ranges = ran
    testnet_xml.set_ip(ipxml)


def run(test, params, env):
    """
    Test command: virsh net-define/net-undefine.

    1) Collect parameters&environment info before test
    2) Prepare options for command
    3) Execute command for test
    4) Check state of defined network
    5) Recover environment
    6) Check result
    """
    uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                      "default"))
    net_name = params.get("net_define_undefine_net_name", "default")
    net_uuid = params.get("net_define_undefine_net_uuid", "")
    options_ref = params.get("net_define_undefine_options_ref", "default")
    trans_ref = params.get("net_define_undefine_trans_ref", "trans")
    extra_args = params.get("net_define_undefine_extra", "")
    remove_existing = params.get("net_define_undefine_remove_existing", "yes")
    status_error = "yes" == params.get("status_error", "no")
    check_states = "yes" == params.get("check_states", "no")
    net_persistent = "yes" == params.get("net_persistent")
    net_active = "yes" == params.get("net_active")
    expect_msg = params.get("net_define_undefine_err_msg")

    # define multi ip/dhcp sections in network
    multi_ip = "yes" == params.get("multi_ip", "no")
    netmask = params.get("netmask")
    prefix_v6 = params.get("prefix_v6")
    single_v6_range = "yes" == params.get("single_v6_range", "no")
    # Get 2nd ipv4 dhcp range
    dhcp_ranges_start = params.get("dhcp_ranges_start", None)
    dhcp_ranges_end = params.get("dhcp_ranges_end", None)

    # Get 2 groups of ipv6 ip address and dhcp section
    address_v6_1 = params.get("address_v6_1")
    dhcp_ranges_v6_start_1 = params.get("dhcp_ranges_v6_start_1", None)
    dhcp_ranges_v6_end_1 = params.get("dhcp_ranges_v6_end_1", None)

    address_v6_2 = params.get("address_v6_2")
    dhcp_ranges_v6_start_2 = params.get("dhcp_ranges_v6_start_2", None)
    dhcp_ranges_v6_end_2 = params.get("dhcp_ranges_v6_end_2", None)

    # Edit net xml forward/ip part then define/start to check invalid setting
    edit_xml = "yes" == params.get("edit_xml", "no")
    address_v4 = params.get("address_v4")
    nat_port_start = params.get("nat_port_start")
    nat_port_end = params.get("nat_port_end")
    test_port = "yes" == params.get("test_port", "no")
    loop = int(params.get("loop", 1))

    virsh_dargs = {'uri': uri, 'debug': False, 'ignore_status': True}
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

    # Prepare environment and record current net_state_dict
    backup = network_xml.NetworkXML.new_all_networks_dict(virsh_instance)
    backup_state = virsh_instance.net_state_dict()
    logging.debug("Backed up network(s): %s", backup_state)

    # Make some XML to use for testing, for now we just copy 'default'
    test_xml = xml_utils.TempXMLFile()  # temporary file
    try:
        # LibvirtXMLBase.__str__ returns XML content
        test_xml.write(str(backup['default']))
        test_xml.flush()
    except (KeyError, AttributeError):
        test.cancel("Test requires default network to exist")

    testnet_xml = get_network_xml_instance(virsh_dargs, test_xml, net_name,
                                           net_uuid, bridge=None)

    if remove_existing:
        for netxml in list(backup.values()):
            netxml.orbital_nuclear_strike()

    # Test both define and undefine, So collect info
    # both of them for result check.
    # When something wrong with network, set it to 1
    fail_flag = 0
    result_info = []

    if options_ref == "correct_arg":
        define_options = testnet_xml.xml
        undefine_options = net_name
    elif options_ref == "no_option":
        define_options = ""
        undefine_options = ""
    elif options_ref == "not_exist_option":
        define_options = "/not/exist/file"
        undefine_options = "NOT_EXIST_NETWORK"

    define_extra = undefine_extra = extra_args
    if trans_ref != "define":
        define_extra = ""

    if params.get('setup_libvirt_polkit') == 'yes':
        virsh_dargs = {'uri': virsh_uri, 'unprivileged_user': unprivileged_user,
                       'debug': False, 'ignore_status': True}
        cmd = "chmod 666 %s" % testnet_xml.xml
        process.run(cmd, shell=True)

    if params.get('net_define_undefine_readonly', 'no') == 'yes':
        virsh_dargs = {'uri': uri, 'debug': False, 'ignore_status': True,
                       'readonly': True}
    try:
        if edit_xml:
            ipxml_v4 = network_xml.IPXML()
            ipxml_v4.address = address_v4
            ipxml_v4.netmask = netmask
            range_4 = network_xml.RangeXML()
            range_4.attrs = {"start": dhcp_ranges_start, "end": dhcp_ranges_end}
            ipxml_v4.dhcp_ranges = range_4
            testnet_xml.del_ip()
            testnet_xml.set_ip(ipxml_v4)
            if test_port:
                nat_port = {"start": nat_port_start, "end": nat_port_end}
                testnet_xml.nat_port = nat_port
            testnet_xml.debug_xml()
        if multi_ip:
            # Enabling IPv6 forwarding with RA routes without accept_ra set to 2
            # is likely to cause routes loss
            sysctl_cmd = 'sysctl net.ipv6.conf.all.accept_ra'
            original_accept_ra = process.run(sysctl_cmd + ' -n').stdout_text
            if original_accept_ra != '2':
                process.system(sysctl_cmd + '=2')
            # add another ipv4 address and dhcp range
            set_ip_section(testnet_xml, address_v4, ipv6=False,
                           netmask=netmask,
                           dhcp_ranges_start=dhcp_ranges_start,
                           dhcp_ranges_end=dhcp_ranges_end)
            # add ipv6 address and dhcp range
            set_ip_section(testnet_xml, address_v6_1, ipv6=True,
                           prefix_v6=prefix_v6,
                           dhcp_ranges_start=dhcp_ranges_v6_start_1,
                           dhcp_ranges_end=dhcp_ranges_v6_end_1)
            # 2nd ipv6 address and dhcp range
            set_ip_section(testnet_xml, address_v6_2, ipv6=True,
                           prefix_v6=prefix_v6,
                           dhcp_ranges_start=dhcp_ranges_v6_start_2,
                           dhcp_ranges_end=dhcp_ranges_v6_end_2)
        testnet_xml.debug_xml()
        # Run test case
        while loop:
            try:
                define_result = virsh.net_define(define_options, define_extra,
                                                 **virsh_dargs)
                logging.debug(define_result)
                define_status = define_result.exit_status

                # Check network states after define
                if check_states and not define_status:
                    net_state = virsh_instance.net_state_dict()
                    if (net_state[net_name]['active'] or
                            net_state[net_name]['autostart'] or
                            not net_state[net_name]['persistent']):
                        fail_flag = 1
                        result_info.append("Found wrong network states for "
                                           "defined netowrk: %s" % str(net_state))

                if define_status == 1 and status_error and expect_msg:
                    libvirt.check_result(define_result, expect_msg.split(';'))

                # If defining network succeed, then trying to start it.
                if define_status == 0:
                    start_result = virsh.net_start(net_name, extra="", **virsh_dargs)
                    logging.debug(start_result)
                    start_status = start_result.exit_status

                if trans_ref == "trans":
                    if define_status:
                        fail_flag = 1
                        result_info.append("Define network with right command failed.")
                    else:
                        if start_status:
                            fail_flag = 1
                            result_info.append("Found wrong network states for "
                                               "defined netowrk: %s" % str(net_state))

                # Check network states after start
                if check_states and not status_error:
                    net_state = virsh_instance.net_state_dict()
                    if (not net_state[net_name]['active'] or
                            net_state[net_name]['autostart'] or
                            not net_state[net_name]['persistent']):
                        fail_flag = 1
                        result_info.append("Found wrong network states for "
                                           "started netowrk: %s" % str(net_state))
                    # Try to set autostart
                    virsh.net_autostart(net_name, **virsh_dargs)
                    net_state = virsh_instance.net_state_dict()
                    if not net_state[net_name]['autostart']:
                        fail_flag = 1
                        result_info.append("Failed to set autostart for network %s"
                                           % net_name)
                    # Restart libvirtd and check state
                    # Close down persistent virsh session before libvirtd restart
                    if hasattr(virsh_instance, 'close_session'):
                        virsh_instance.close_session()
                    libvirtd = utils_libvirtd.Libvirtd()
                    libvirtd.restart()
                    # Need to redefine virsh_instance after libvirtd restart
                    virsh_instance = virsh.VirshPersistent(**virsh_dargs)
                    net_state = virsh_instance.net_state_dict()
                    if (not net_state[net_name]['active'] or
                            not net_state[net_name]['autostart']):
                        fail_flag = 1
                        result_info.append("Found wrong network state after restarting"
                                           " libvirtd: %s" % str(net_state))
                    logging.debug("undefine network:")
                    # prepare the network status
                    if not net_persistent:
                        virsh.net_undefine(net_name, ignore_status=False)
                    if not net_active:
                        virsh.net_destroy(net_name, ignore_status=False)
                    undefine_status = virsh.net_undefine(undefine_options, undefine_extra,
                                                         **virsh_dargs).exit_status

                    net_state = virsh_instance.net_state_dict()
                    if net_persistent:
                        if undefine_status:
                            fail_flag = 1
                            result_info.append("undefine should succeed but failed")
                        if net_active:
                            if (not net_state[net_name]['active'] or
                                    net_state[net_name]['autostart'] or
                                    net_state[net_name]['persistent']):
                                fail_flag = 1
                                result_info.append("Found wrong network states for "
                                                   "undefined netowrk: %s" % str(net_state))
                        else:
                            if net_name in net_state:
                                fail_flag = 1
                                result_info.append("Transient network should not exists "
                                                   "after undefine : %s" % str(net_state))
                    else:
                        if not undefine_status:
                            fail_flag = 1
                            result_info.append("undefine transient network should fail "
                                               "but succeed: %s" % str(net_state))
                # Stop network for undefine test anyway
                destroy_result = virsh.net_destroy(net_name, extra="", **virsh_dargs)
                logging.debug(destroy_result)

                # Undefine network
                if not check_states:
                    undefine_result = virsh.net_undefine(undefine_options, undefine_extra,
                                                         **virsh_dargs)
                    if trans_ref != "define":
                        logging.debug(undefine_result)
                    undefine_status = undefine_result.exit_status
            except Exception:
                logging.debug("The define and undefine operation in loop %s failed. ", loop)
            finally:
                loop = loop - 1

    finally:
        # Recover environment
        leftovers = network_xml.NetworkXML.new_all_networks_dict(
            virsh_instance)
        for netxml in list(leftovers.values()):
            netxml.orbital_nuclear_strike()

        # Recover from backup
        for netxml in list(backup.values()):
            netxml.sync(backup_state[netxml.name])

        # Close down persistent virsh session (including for all netxml copies)
        if hasattr(virsh_instance, 'close_session'):
            virsh_instance.close_session()

        # Done with file, cleanup
        del test_xml
        del testnet_xml

    # Check status_error
    # If fail_flag is set, it must be transaction test.
    if fail_flag:
        test.fail("Define network for transaction test "
                  "failed:%s" % result_info)

    # The logic to check result:
    # status_error&only undefine:it is negative undefine test only
    # status_error&(no undefine):it is negative define test only
    # (not status_error)&(only undefine):it is positive transaction test.
    # (not status_error)&(no undefine):it is positive define test only
    if status_error:
        if trans_ref == "undefine":
            if undefine_status == 0:
                test.fail("Run successfully with wrong command.")
        else:
            if define_status == 0:
                if start_status == 0:
                    test.fail("Define an unexpected network, "
                              "and start it successfully.")
                else:
                    test.fail("Define an unexpected network, "
                              "but start it failed.")
    else:
        if trans_ref == "undefine":
            if undefine_status:
                test.fail("Define network for transaction "
                          "successfully, but undefine failed.")
        else:
            if define_status != 0:
                test.fail("Run failed with right command")
            else:
                if start_status != 0:
                    test.fail("Network is defined as expected, "
                              "but start it failed.")
