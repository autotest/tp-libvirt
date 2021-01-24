import os
import logging

from virttest import virsh
from virttest import utils_libvirtd
from virttest import libvirt_version
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh net-autostart.
    """
    # Gather test parameters
    status_error = "yes" == params.get("status_error", "no")
    net_ref = params.get("net_autostart_net_ref", "netname")
    disable = "yes" == params.get("net_autostart_disable", "no")
    extra = params.get("net_autostart_extra", "")  # extra cmd-line params.
    net_name = params.get("net_autostart_net_name", "autotest")
    net_transient = "yes" == params.get("net_transient", "no")
    readonly = ("yes" == params.get("readonly", "no"))
    sim_reboot = "yes" == params.get("sim_reboot", "no")

    # Prepare environment and record current net_state_dict
    backup = network_xml.NetworkXML.new_all_networks_dict()
    backup_state = virsh.net_state_dict()
    logging.debug("Backed up network(s): %s", backup_state)

    # Generate our own bridge
    # First check if a bridge of this name already exists
    try:
        _ = backup[net_name]
    except (KeyError, AttributeError):
        pass  # Not found - good
    else:
        test.cancel("Found network bridge '%s' - skipping" % net_name)

    # Define a very bare bones bridge, don't provide UUID - use whatever
    # libvirt ends up generating.  We need to define a persistent network
    # since we'll be looking to restart libvirtd as part of this test.
    #
    # This test cannot use the 'default' bridge (virbr0) since undefining
    # it causes issues for libvirtd restart since it's expected that a
    # default network is defined
    #
    temp_bridge = """
<network>
   <name>%s</name>
   <bridge name="vir%sbr0"/>
</network>
""" % (net_name, net_name)

    try:
        test_xml = network_xml.NetworkXML(network_name=net_name)
        test_xml.xml = temp_bridge
        test_xml.define()
    except xcepts.LibvirtXMLError as detail:
        test.error("Failed to define a test network.\n"
                   "Detail: %s." % detail)

    # To guarantee cleanup will be executed
    try:
        # Run test case

        # Get the updated list and make sure our new bridge exists
        currents = network_xml.NetworkXML.new_all_networks_dict()
        current_state = virsh.net_state_dict()
        logging.debug("Current network(s): %s", current_state)
        try:
            testbr_xml = currents[net_name]
        except (KeyError, AttributeError):
            test.error("Did not find newly defined bridge '%s'" % net_name)

        # Prepare default property for network
        # Transient network can not be set autostart
        # So confirm persistent is true for test
        testbr_xml['persistent'] = True

        # Set network to inactive
        # Since we do not reboot host to check(instead of restarting libvirtd)
        # If default network is active, we cannot check "--disable".
        # Because active network will not be inactive after restarting libvirtd
        # even we set autostart to False. While inactive network will be active
        # after restarting libvirtd if we set autostart to True
        testbr_xml['active'] = False

        # Prepare options and arguments
        if net_ref == "netname":
            net_ref = testbr_xml.name
        elif net_ref == "netuuid":
            net_ref = testbr_xml.uuid

        if disable:
            net_ref += " --disable"

        if net_transient:
            # make the network to transient and active
            ret = virsh.net_start(net_name)
            libvirt.check_exit_status(ret)
            ret = virsh.net_undefine(net_name)
            libvirt.check_exit_status(ret)
            logging.debug("after make it transistent: %s" % virsh.net_state_dict())

        # Run test case
        # Use function in virsh module directly for both normal and error test
        result = virsh.net_autostart(net_ref, extra, readonly=readonly)
        status = result.exit_status
        if status:
            logging.debug("autostart cmd return:\n%s" % result.stderr.strip())
        else:
            logging.debug("set autostart succeed: %s" % virsh.net_state_dict())

        # Check if autostart or disable is successful with libvirtd restart.
        # TODO: Since autostart is designed for host reboot,
        #       we'd better check it with host reboot.
        autostart_file = '/var/run/libvirt/network/autostarted'
        check_version = libvirt_version.version_compare(5, 6, 0)
        if check_version and os.path.exists(autostart_file):
            logging.debug("the sim_reboot is %s" % sim_reboot)
            if sim_reboot:
                os.unlink(autostart_file)
        utils_libvirtd.libvirtd_restart()

        # Reopen testbr_xml
        currents = network_xml.NetworkXML.new_all_networks_dict()
        current_state = virsh.net_state_dict()
        logging.debug("After libvirtd reboot, current network(s): %s", current_state)
        testbr_xml = currents[net_name]
        is_active = testbr_xml['active']
        # undefine the persistent&autostart network,
        # if "autostart" should change to 'no"
        if not disable and not net_transient:
            logging.debug("undefine the persistent/autostart network:")
            ret = virsh.net_undefine(net_name)
            libvirt.check_exit_status(ret)
            # after undefine, the network can not be "autostart"
            if net_name in virsh.net_list("").stdout.strip():
                current_state = virsh.net_state_dict()[net_name]
                logging.debug("Current network(s): %s", current_state)
                net_autostart_now = current_state['autostart']
                if not status_error and not disable and net_autostart_now:
                    test.fail("transient network can not be autostart")

    finally:
        persistent_net = virsh.net_list("--persistent --all").stdout.strip()
        if net_name in persistent_net:
            virsh.net_undefine(net_name)
        active_net = virsh.net_list("").stdout.strip()
        if net_name in active_net:
            virsh.net_destroy(net_name)

    # Check Result
    if status_error:
        if status == 0:
            test.fail("Run successfully with wrong command!")
    else:
        if disable:
            if status or is_active:
                test.fail("Disable autostart failed.")
        else:
            if status:
                test.fail("The virsh cmd return eror when enable autostart!")
            # If host reboot(sim_reboot=True), the network should be active
            # If host do not reboot, restart libvirtd will not start inactive
            # autostart network after libvirt 5.6.0
            if sim_reboot:
                if not is_active:
                    test.fail("Set network autostart failed.")
            else:
                if check_version and is_active:
                    test.fail("net turn active with libvirtd restart without host reboot!")
