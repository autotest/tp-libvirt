import random
import re

from avocado.utils import process
from virttest import (
    env_process,
    error_context,
    utils_misc,
    utils_net,
    utils_test,
)
from virttest.utils_test import libvirt


@error_context.context_aware
def run(test, params, env):
    """
    Test the RX jumbo frame function of vnics:

    1) Boot the VM.
    2) Change the MTU of guest nics and host taps depending on the NIC model.
    3) Add the static ARP entry for guest NIC.
    4) Wait for the MTU ok.
    5) Verify the path MTU using ping.
    6) Ping the guest with large frames.
    7) Increment size ping.
    8) Flood ping the guest with large frames.
    9) Verify the path MTU.
    10) Recover the MTU.

    :param test: LIBVIRT test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    def get_ovs_ports(ovs):
        """
        get the ovs bridge all Interface list.

        :param ovs: Ovs bridge name
        """
        cmd = "ovs-vsctl list-ports %s" % ovs
        return process.getoutput(cmd, shell=True)

    netdst = params.get("netdst", "switch")
    host_bridges = utils_net.find_bridge_manager(netdst)
    if not isinstance(host_bridges, utils_net.Bridge):
        host_hw_interface = get_ovs_ports(netdst)
        tmp_ports = re.findall(r"t[0-9]{1,}-[a-zA-Z0-9]{6}", host_hw_interface)
        if tmp_ports:
            for p in tmp_ports:
                process.system_output("ovs-vsctl del-port %s %s" % (netdst, p))

    params["start_vm"] = "yes"
    env_process.preprocess_vm(test, params, env, params["main_vm"])

    timeout = params.get_numeric("login_timeout", 360)
    mtu_default = 1500
    mtu = params.get_numeric("mtu", "1500")
    def_max_icmp_size = mtu - 28
    max_icmp_pkt_size = params.get_numeric("max_icmp_pkt_size", def_max_icmp_size)
    flood_time = params.get_numeric("flood_time", "300")
    os_type = params.get("os_type")
    hint = params.get("hint", "do")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()
    session = vm.wait_for_login(timeout=timeout)
    session_serial = vm.wait_for_serial_login(timeout=timeout)

    iface_mac = vm.get_mac_address(0)
    ifname = libvirt.get_ifname_host(vm_name, iface_mac)
    guest_ip = vm.get_address(0)
    if guest_ip is None:
        test.error("Could not get the guest ip address")

    host_mtu_cmd = "ip link set dev %s mtu %s"
    if not isinstance(host_bridges, utils_net.Bridge):
        target_ifaces = set(get_ovs_ports(netdst).splitlines())
    else:
        br_in_use = host_bridges.list_br()
        ifaces_in_use = host_bridges.list_iface()
        target_ifaces = set(ifaces_in_use) - set(br_in_use)

    error_context.context("Change all Bridge NICs MTU to %s" % mtu, test.log.info)
    for iface in target_ifaces:
        process.run(host_mtu_cmd % (iface, mtu), shell=True)

    try:
        error_context.context("Changing the MTU of guest", test.log.info)
        # Environment preparation
        if os_type == "linux":
            ethname = utils_net.get_linux_ifname(session, iface_mac)
            guest_mtu_cmd = "ip link set dev %s mtu %s" % (ethname, mtu)
        else:
            connection_id = utils_net.get_windows_nic_attribute(
                session, "macaddress", iface_mac, "netconnectionid"
            )

            index = utils_net.get_windows_nic_attribute(
                session, "netconnectionid", connection_id, "index"
            )

            reg_set_mtu_pattern = params.get("reg_mtu_cmd")
            mtu_key_word = params.get("mtu_key", "MTU")
            reg_set_mtu = reg_set_mtu_pattern % (int(index), mtu_key_word, int(mtu))
            guest_mtu_cmd = "%s " % reg_set_mtu

        session.cmd(guest_mtu_cmd)
        if os_type == "windows":
            mode = "netsh"
            utils_net.restart_windows_guest_network(
                session_serial, connection_id, mode=mode
            )

        error_context.context("Changing the MTU of host tap ...", test.log.info)
        process.run(host_mtu_cmd % (ifname, mtu), shell=True)

        error_context.context("Add a temporary static ARP entry ...", test.log.info)
        arp_add_cmd = "arp -s %s %s -i %s" % (guest_ip, iface_mac, ifname)
        process.run(arp_add_cmd, shell=True)

        def is_mtu_ok():
            status, _ = utils_test.ping(
                guest_ip, 1, packetsize=max_icmp_pkt_size, hint="do", timeout=2
            )
            return status == 0

        def verify_mtu():
            test.log.info("Verify the path MTU")
            status, output = utils_test.ping(
                guest_ip, 10, packetsize=max_icmp_pkt_size, hint="do", timeout=15
            )
            if status != 0:
                test.log.error(output)
                test.fail("Path MTU is not as expected")
            if utils_test.get_loss_ratio(output) != 0:
                test.log.error(output)
                test.fail("Packet loss ratio during MTU verification is not zero")

        def flood_ping():
            test.log.info("Flood with large frames")
            utils_test.ping(
                guest_ip,
                packetsize=max_icmp_pkt_size,
                flood=True,
                timeout=flood_time,
            )

        def large_frame_ping(count=100):
            test.log.info("Large frame ping")
            _, output = utils_test.ping(
                guest_ip, count, packetsize=max_icmp_pkt_size, timeout=float(count) * 2
            )
            ratio = utils_test.get_loss_ratio(output)
            if ratio != 0:
                test.fail("Loss ratio of large frame ping is %s" % ratio)

        def size_increase_ping(step=None):
            test.log.info("Size increase ping")
            if step is None:
                step = random.randrange(90, 110)
            for size in range(0, max_icmp_pkt_size + 1, step):
                test.log.info("Ping %s with size %s", guest_ip, size)
                status, output = utils_test.ping(
                    guest_ip, 1, packetsize=size, hint=hint, timeout=1
                )
                if status != 0:
                    status, output = utils_test.ping(
                        guest_ip,
                        10,
                        packetsize=size,
                        adaptive=True,
                        hint=hint,
                        timeout=20,
                    )

                    fail_ratio = params.get_numeric("fail_ratio", 50)
                    loss = utils_test.get_loss_ratio(output)
                    if loss > fail_ratio:
                        test.fail(
                            "Ping loss ratio %s is greater than %s%% for size %s"
                            % (loss, fail_ratio, size)
                        )

        test.log.info("Waiting for the MTU to be OK")
        wait_mtu_ok = 20
        if not utils_misc.wait_for(is_mtu_ok, wait_mtu_ok, 0, 1):
            test.log.debug(
                process.getoutput(
                    "ifconfig -a", verbose=False, ignore_status=True, shell=True
                )
            )
            test.error("MTU is not as expected even after %s seconds" % wait_mtu_ok)

        # Functional Test
        error_context.context("Checking whether MTU change is ok", test.log.info)

        verify_mtu()
        large_frame_ping()
        size_increase_ping()

        # Stress test
        flood_ping()
        verify_mtu()

    finally:
        # Environment clean
        if session:
            session.close()
        if 'session_serial' in locals() and session_serial:
            session_serial.close()
        grep_cmd = "grep '%s.*%s' /proc/net/arp" % (guest_ip, ifname)
        if process.system(grep_cmd, shell=True) == 0:
            process.run("arp -d %s -i %s" % (guest_ip, ifname), shell=True)
            test.log.info("Removing the temporary ARP entry successfully")

        test.log.info("Change back Bridge NICs MTU to %s", mtu_default)
        for iface in target_ifaces:
            process.run(host_mtu_cmd % (iface, mtu_default), shell=True)
