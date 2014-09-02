import os
import logging

from autotest.client.shared import error
from autotest.client import utils
from virttest.utils_test import libvirt
from virttest.staging import service
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh

NETWORK_SCRIPT = "/etc/sysconfig/network-scripts/ifcfg-"


def run(test, params, env):
    """
    Test virsh iface-bridge and iface-unbridge commands.

    (1) Bridge an existing network device(iface-bridge).
    (2) Unbridge a network device(iface-unbridge).
    """

    iface_name = params.get("iface_name")
    bridge_name = params.get("bridge_name")
    ping_ip = params.get("ping_ip", "")
    ping_count = int(params.get("ping_count", "3"))
    ping_timeout = int(params.get("ping_timeout", "5"))
    bridge_option = params.get("bridge_option")
    unbridge_option = params.get("unbridge_option")
    bridge_delay = "yes" == params.get("bridge_delay", "no")
    delay_num = params.get("delay_num", "0")
    create_bridge = "yes" == params.get("create_bridge", "yes")
    bridge_status_error = "yes" == params.get("bridge_status_error", "no")
    unbridge_status_error = "yes" == params.get("unbridge_status_error", "no")
    iface_script = NETWORK_SCRIPT + iface_name
    iface_script_bk = os.path.join(test.tmpdir, "iface-%s.bk" % iface_name)
    check_iface = "yes" == params.get("check_iface", "yes")
    if check_iface:
        if not libvirt.check_iface(iface_name, "exists", "--all"):
            raise error.TestNAError("Interface '%s' not exists" % iface_name)
        net_iface = utils_net.Interface(name=iface_name)
        iface_is_up = net_iface.is_up()

        # Make sure the interface exists
        if not libvirt.check_iface(iface_name, "exists", "--all"):
            raise error.TestNAError("Interface '%s' not exists" % iface_name)

        # Back up the interface script
        utils.run("cp %s %s" % (iface_script, iface_script_bk))

    # Make sure the bridge name not exists
    net_bridge = utils_net.Bridge()
    if bridge_name in net_bridge.list_br():
        raise error.TestNAError("Bridge '%s' already exists" % bridge_name)

    # Stop NetworkManager service
    try:
        NM = utils_misc.find_command("NetworkManager")
    except ValueError:
        logging.debug("No NetworkManager service.")
        NM = None
    NM_is_running = False
    if NM is not None:
        NM_service = service.Factory.create_service("NetworkManager")
        NM_is_running = NM_service.status()
        if NM_is_running:
            NM_service.stop()

    def unbridge_check():
        """
        Check the result after do unbridge.
        """
        list_option = "--all"
        if libvirt.check_iface(bridge_name, "exists", list_option):
            raise error.TestFail("%s is still present." % bridge_name)
        if "no-start" in unbridge_option:
            list_option = "--inactive"
        if not libvirt.check_iface(iface_name, "exists", list_option):
            raise error.TestFail("%s is not present." % iface_name)

    if bridge_delay:
        bridge_option += " --delay %s" % delay_num
    # Run test
    try:
        if create_bridge:
            # Create bridge
            result = virsh.iface_bridge(iface_name, bridge_name, bridge_option)
            libvirt.check_exit_status(result, bridge_status_error)
            if not bridge_status_error:
                # Get the new create bridge IP address
                try:
                    br_ip = utils_net.get_ip_address_by_interface(bridge_name)
                except:
                    br_ip = ""
                # Do ping test only bridge has IP address and ping_ip not empty
                if br_ip and ping_ip:
                    if not libvirt.check_iface(bridge_name, "ping", ping_ip,
                                               count=ping_count, timeout=ping_timeout):
                        raise error.TestFail("Fail to ping %s from %s."
                                             % (ping_ip, bridge_name))
                else:
                    # Skip ping test
                    logging.debug("Skip ping test as %s has no IP address",
                                  bridge_name)
                list_option = ""
                if "no-start" in bridge_option:
                    list_option = "--inactive"
                if libvirt.check_iface(bridge_name, "exists", list_option):
                    # Unbridge
                    result = virsh.iface_unbridge(bridge_name, unbridge_option)
                    libvirt.check_exit_status(result, unbridge_status_error)
                    if not unbridge_status_error:
                        unbridge_check()
                else:
                    raise error.TestFail("%s is not present." % bridge_name)
        else:
            # Unbridge without creating bridge, only for negative test now
            result = virsh.iface_unbridge(bridge_name, unbridge_option)
            libvirt.check_exit_status(result, unbridge_status_error)
            if not unbridge_status_error:
                unbridge_check()
    finally:
        if create_bridge and check_iface:
            if libvirt.check_iface(bridge_name, "exists", "--all"):
                virsh.iface_unbridge(bridge_name)
            if not os.path.exists(iface_script):
                utils.run("mv %s %s" % (iface_script_bk, iface_script))
            if iface_is_up:
                # Need reload script
                utils.run("ifup %s" % iface_name)
            else:
                net_iface.down()
            # Clear the new create bridge if it exists
            try:
                utils_net.bring_down_ifname(bridge_name)
                utils.run("brctl delbr %s" % bridge_name)
            except utils_net.TAPBringDownError:
                pass
        if NM_is_running:
            NM_service.start()
