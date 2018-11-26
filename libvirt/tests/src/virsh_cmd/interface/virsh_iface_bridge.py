import os
import logging

from avocado.utils import process

from avocado.utils import path as utils_path

from virttest import utils_net
from virttest import data_dir
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.staging import service
from virttest import utils_package

NETWORK_SCRIPT = "/etc/sysconfig/network-scripts/ifcfg-"


def run(test, params, env):
    """
    Test virsh iface-bridge and iface-unbridge commands.

    (1) Bridge an existing network device(iface-bridge).
    (2) Unbridge a network device(iface-unbridge).
    """

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
    iface_name = params.get("iface_name")
    if iface_name == "HOST_INTERFACE":
        # Get the first active nic to test
        iface_name = utils_net.get_net_if(state="UP")[0]
        iface_ip = utils_net.get_ip_address_by_interface(iface_name)

    iface_script = NETWORK_SCRIPT + iface_name
    iface_script_bk = os.path.join(data_dir.get_tmp_dir(), "iface-%s.bk" % iface_name)
    bridge_script = NETWORK_SCRIPT + bridge_name
    check_iface = "yes" == params.get("check_iface", "yes")

    if check_iface:
        # Make sure the interface exists
        if not libvirt.check_iface(iface_name, "exists", "--all"):
            test.cancel("Interface '%s' not exists" % iface_name)

        # Back up the interface script
        process.run("cp %s %s" % (iface_script, iface_script_bk), shell=True)

    # Make sure the bridge name not exists
    net_bridge = utils_net.Bridge()
    if bridge_name in net_bridge.list_br():
        test.cancel("Bridge '%s' already exists" % bridge_name)

    # Stop NetworkManager service
    NM_service = None
    try:
        NM = utils_path.find_command("NetworkManager")
    except utils_path.CmdNotFoundError:
        logging.debug("No NetworkManager service.")
        NM = None
    if NM is not None:
        NM_service = service.Factory.create_service("NetworkManager")
        NM_is_running = NM_service.status()
        if NM_is_running:
            logging.debug("NM_is_running:%s", NM_is_running)
            logging.debug("Stop NetworkManager...")
            NM_service.stop()

    # Start network service
    NW_service = service.Factory.create_service("network")
    NW_status = NW_service.status()
    if NW_status is None:
        logging.debug("network service not found")
        if (not utils_package.package_install('network-scripts') or
                not utils_package.package_install('initscripts')):
            test.cancel("Failed to install network service")
    if NW_status is not True:
        logging.debug("network service is not running")
        logging.debug("Start network service...")
        NW_service.start()

    def unbridge_check():
        """
        Check the result after do unbridge.
        """
        list_option = "--all"
        if libvirt.check_iface(bridge_name, "exists", list_option):
            test.fail("%s is still present." % bridge_name)
        if "no-start" in unbridge_option:
            list_option = "--inactive"
        if not libvirt.check_iface(iface_name, "exists", list_option):
            test.fail("%s is not present." % iface_name)

    if bridge_delay:
        bridge_option += " --delay %s" % delay_num
    # Run test
    try:
        if create_bridge:
            # Create bridge
            # Set timeout as the cmd run a long time
            result = virsh.iface_bridge(iface_name, bridge_name, bridge_option, timeout=240, debug=True)
            libvirt.check_exit_status(result, bridge_status_error)
            if not bridge_status_error:
                # Get the new create bridge IP address
                try:
                    br_ip = utils_net.get_ip_address_by_interface(bridge_name)
                except Exception:
                    br_ip = ""
                # check IP of new bridge
                if check_iface and br_ip and br_ip != iface_ip:
                    test.fail("bridge IP(%s) isn't the same as iface IP(%s)."
                              % (br_ip, iface_ip))
                # check the status of STP feature
                if "no-start" not in bridge_option:
                    if "no-stp" not in bridge_option:
                        if "yes" != net_bridge.get_stp_status(bridge_name):
                            test.fail("Fail to enable STP.")
                # Do ping test only bridge has IP address and ping_ip not empty
                if br_ip and ping_ip:
                    if not libvirt.check_iface(bridge_name, "ping", ping_ip,
                                               count=ping_count, timeout=ping_timeout):
                        test.fail("Fail to ping %s from %s."
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
                    result = virsh.iface_unbridge(bridge_name, unbridge_option, timeout=60)
                    libvirt.check_exit_status(result, unbridge_status_error)
                    if not unbridge_status_error:
                        unbridge_check()
                else:
                    test.fail("%s is not present." % bridge_name)
        else:
            # Unbridge without creating bridge, only for negative test now
            result = virsh.iface_unbridge(bridge_name, unbridge_option)
            libvirt.check_exit_status(result, unbridge_status_error)
            if not unbridge_status_error:
                unbridge_check()
    finally:
        if create_bridge and check_iface:
            if libvirt.check_iface(bridge_name, "exists", "--all"):
                virsh.iface_unbridge(bridge_name, timeout=60)
            if os.path.exists(iface_script_bk):
                process.run("mv %s %s" % (iface_script_bk, iface_script), shell=True)
            if os.path.exists(bridge_script):
                process.run("rm -rf %s" % bridge_script, shell=True)
            # Clear the new create bridge if it still exists
            try:
                utils_net.bring_down_ifname(bridge_name)
                process.run("ip link del dev %s" % bridge_name, shell=True)
            except utils_net.TAPBringDownError:
                pass
            # Reload network configuration
            NW_service.restart()
            # Recover NetworkManager
            if NM_is_running:
                NM_service.start()
