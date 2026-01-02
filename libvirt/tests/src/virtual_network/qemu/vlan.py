import time

import aexpect
from virttest import error_context, utils_net, utils_test
from virttest.libvirt_xml import vm_xml


@error_context.context_aware
def run(test, params, env):
    """
    Test 802.1Q vlan of NIC.

    For Linux guest:
    1) Create two VMs.
    2) load 8021q module in guest.
    3) Setup vlans by ip in guest and using hard-coded ip address.
    4) Enable arp_ignore for all ipv4 device in guest.
    5) Repeat steps 2 - 4 in every guest.
    6) Test by ping between same and different vlans of two VMs.
    7) Test by flood ping between same vlan of two VMs.
    8) Test by TCP data transfer between same vlan of two VMs.
    9) Remove the named vlan-device.
    10) Test maximal plumb/unplumb vlans.

    For Windows guest:
    1) Create two VMs.
    2) Set vlan tag in every guest and guest will get subnet ip(169.254)
       automatically.
    3) Test by ping between same vlan of two VMs.

    :param test: Libvirt test object.
    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """

    def add_vlan(test, session, v_id, iface="eth0", cmd_type="ip"):
        """
        Creates a VLAN device on a given network interface in the guest.

        :param test: The test instance.
        :param session: The guest session to execute commands on.
        :param v_id: The VLAN ID (e.g., 100).
        :param iface: The parent network interface (e.g., "eth0").
        :param cmd_type: The command type to use "ip".
        """
        vlan_if = "%s.%s" % (iface, v_id)
        txt = "Create vlan interface '%s' on %s" % (vlan_if, iface)
        error_context.context(txt, test.log.info)
        cmd = ""
        if cmd_type == "ip":
            v_name = "%s.%s" % (iface, v_id)
            cmd = "ip link add link %s %s type vlan id %s " % (iface, v_name, v_id)
            if session.cmd_status(cmd) == 0:
                session.cmd("ip link set dev %s up" % v_name)
                return True
            return False
        else:
            err_msg = "Unexpected vlan operation command: %s, " % cmd_type
            test.error(err_msg)

    def set_ip_vlan(session, v_id, vlan_ip, iface="eth0", prefixlen="24"):
        """
        Assigns an IP address to a VLAN interface.

        :param session: The guest session to execute commands on.
        :param v_id: The VLAN ID of the target interface.
        :param vlan_ip: The IP address to assign.
        :param iface: The parent network interface (e.g., "eth0").
        """
        iface = "%s.%s" % (iface, v_id)
        txt = "Assign IP '%s/%s' to vlan interface '%s'" % (vlan_ip, prefixlen, iface)
        error_context.context(txt, test.log.info)
        session.cmd("ip addr add %s/%s dev %s" % (vlan_ip, prefixlen, iface))
        session.cmd("ip link set dev %s up" % iface)

    def set_arp_ignore(session):
        """
        Enables arp_ignore for all IPv4 devices in the guest.

        This prevents the guest from replying to ARP requests on one interface
        for an IP address that is configured on another interface, which is
        important when multiple VLANs are active.

        :param session: The guest session to execute commands on.
        """
        error_context.context(
            "Enable arp_ignore for all ipv4 device in guest", test.log.info
        )
        ignore_cmd = "echo 1 > /proc/sys/net/ipv4/conf/all/arp_ignore"
        session.cmd(ignore_cmd)

    def rem_vlan(test, session, v_id, iface="eth0", cmd_type="ip"):
        """
        Removes a named VLAN interface from the guest.

        :param test: The test instance.
        :param session: The guest session to execute commands on.
        :param v_id: The VLAN ID of the interface to remove.
        :param iface: The parent network interface (e.g., "eth0").
        :param cmd_type: The command type to use "ip".
        :return: The exit status of the remove command.
        :rtype: int
        """
        v_iface = "%s.%s" % (iface, v_id)
        rem_vlan_cmd = ""
        if cmd_type == "ip":
            rem_vlan_cmd = "ip link delete %s" % v_iface
        else:
            err_msg = "Unexpected vlan operation command: %s, " % cmd_type
            test.error(err_msg)
        error_context.context("Remove vlan interface '%s'." % v_iface, test.log.info)
        return session.cmd_status(rem_vlan_cmd)

    def find_free_port(dst):
        """
        Finds an available TCP/UDP port on the destination guest.

        :param dst: The index of the destination guest VM in the `sessions` list.
        :return: An integer representing a free port number.
        :rtype: int
        """
        check_cmd = "netstat -nultp |awk '(NR>2){print $4}' | awk -F':' '{print $NF}'"
        output = sessions[dst].cmd_output(check_cmd).strip()
        if not output:
            return 1025
        used_ports = {int(p) for p in output.splitlines() if p.isdigit()}
        for port in range(1025, 65536):
            if port not in used_ports:
                return port
        test.error("No free TCP/UDP ports found in guest.")

    def nc_transfer(test, src, dst):
        """
        Transfers a file between two guests using netcat (nc) and verifies
        its integrity via an MD5 checksum.

        :param test: The test instance.
        :param src: The index of the source guest VM in the `sessions` list.
        :param dst: The index of the destination guest VM in the `sessions` list.
        """
        nc_port = find_free_port(dst)
        listen_cmd = params.get("listen_cmd")
        send_cmd = params.get("send_cmd")

        # listen in dst
        listen_cmd = listen_cmd % (nc_port, "receive")
        sessions[dst].sendline(listen_cmd)
        time.sleep(2)
        # send file from src to dst
        send_cmd = send_cmd % (vlan_ip[dst], str(nc_port), "file")
        sessions[src].cmd(send_cmd, timeout=60)
        try:
            sessions[dst].read_up_to_prompt(timeout=60)
        except aexpect.ExpectError:
            # kill server
            session_ctl[dst].cmd_output_safe("killall -9 nc")
            test.fail("Fail to receive file from vm%s to vm%s" % (src + 1, dst + 1))
        # check MD5 message digest of receive file in dst
        digest_receive = sessions[dst].cmd_output("md5sum receive").split()[0]
        if digest_receive == digest_origin[src]:
            test.log.info("File succeed received in vm %s", vlan_ip[dst])
        else:
            test.log.info("Digest_origin is  %s", digest_origin[src])
            test.log.info("Digest_receive is %s", digest_receive)
            test.fail("File transferred differ from origin")
        sessions[dst].cmd("rm -f receive")

    def flood_ping(src, dst):
        """
        Performs a flood ping from a source guest to a destination guest.

        Note: A dedicated session is used for this because aexpect needs to
        close the session to interrupt the running ping process.

        :param src: The index of the source guest VM in the `vms` list.
        :param dst: The index of the destination guest VM in the `vms` list.
        """
        txt = "Flood ping from %s interface %s to %s" % (
            vms[src].name,
            ifname[src],
            vlan_ip[dst],
        )
        error_context.context(txt, test.log.info)
        session_flood = vms[src].wait_for_login(timeout=60)
        utils_test.ping(
            vlan_ip[dst],
            flood=True,
            interface=ifname[src],
            session=session_flood,
            timeout=10,
        )
        session_flood.close()

    vms = []
    sessions = []
    session_ctl = []
    ifname = []
    vm_ip = []
    digest_origin = []
    vlan_ip = ["", ""]
    ip_unit = ["1", "2"]
    subnet = params.get("subnet", "192.168")
    vlan_num = params.get_numeric("vlan_num", 5)
    maximal = params.get_numeric("maximal", 4094)
    file_size = params.get_numeric("file_size", 4096)
    cmd_type = params.get("cmd_type", "ip")
    login_timeout = params.get_numeric("login_timeout", 360)
    set_vlan_cmd = params.get("set_vlan_cmd")
    stop_firewall_cmd = params.get("stop_firewall_cmd")
    load_8021q_cmd = params.get("load_8021q_cmd")
    driver_verifier = params.get("driver_verifier")

    vms.append(env.get_vm(params["main_vm"]))
    vms.append(env.get_vm("vm2"))

    for vm_ in vms:
        vm_.verify_alive()
        test.log.debug("Guest xml:\n%s", vm_xml.VMXML.new_from_dumpxml(vm_.name))

    try:
        for vm_index, vm in enumerate(vms):
            if params["os_type"] == "windows":
                session = vm.wait_for_login(timeout=login_timeout)
                session = utils_test.qemu.windrv_check_running_verifier(
                    session, vm, test, driver_verifier
                )
                session = vm.wait_for_serial_login(timeout=login_timeout)
                dev_mac = vm.virtnet[0].mac
                connection_id = utils_net.get_windows_nic_attribute(
                    session, "macaddress", dev_mac, "netconnectionid"
                )
                session.cmd(set_vlan_cmd % connection_id)
                utils_net.restart_windows_guest_network(session, connection_id)
                time.sleep(10)
                nicid = utils_net.get_windows_nic_attribute(
                    session=session,
                    key="netenabled",
                    value=True,
                    target="netconnectionID",
                )
                ifname.append(nicid)
                vm_ip.append(
                    utils_net.get_guest_ip_addr(
                        session, dev_mac, os_type="windows", linklocal=True
                    )
                )
                test.log.debug("IP address is %s in %s", vm_ip, vm.name)
                session_ctl.append(session)
                continue

            error_context.base_context("Prepare test env on %s" % vm.name)
            session = vm.wait_for_login(timeout=login_timeout)
            if not session:
                err_msg = "Could not log into guest %s" % vm.name
                test.error(err_msg)
            sessions.append(session)
            test.log.info("Logged in %s successful", vm.name)
            session_ctl.append(vm.wait_for_login(timeout=login_timeout))
            ifname.append(utils_net.get_linux_ifname(session, vm.get_mac_address()))
            vm_ip.append(vm.get_address())
            test.log.debug("IP address is %s in %s", vm_ip, vm.name)
            dd_cmd = "dd if=/dev/urandom of=file bs=1M count=%s"
            session.cmd(dd_cmd % file_size)
            digest_origin.append(session.cmd("md5sum file", timeout=60).split()[0])

            session.cmd_output_safe(stop_firewall_cmd)
            error_context.context(
                "Load 8021q module in guest %s" % vm.name, test.log.info
            )
            session.cmd_output_safe(load_8021q_cmd)

            error_context.context(
                "Setup vlan environment in guest %s" % vm.name, test.log.info
            )
            for vlan_i in range(1, vlan_num + 1):
                add_vlan(test, session, vlan_i, ifname[vm_index], cmd_type)
                v_ip = "%s.%s.%s" % (subnet, vlan_i, ip_unit[vm_index])
                set_ip_vlan(session, vlan_i, v_ip, ifname[vm_index])
            set_arp_ignore(session)

        if params["os_type"] == "windows":
            for vm_index in range(len(vms)):
                status, output = utils_test.ping(
                    dest=vm_ip[(vm_index + 1) % 2],
                    count=10,
                    session=session_ctl[vm_index],
                    timeout=30,
                )
                loss = utils_test.get_loss_ratio(output)
                if not loss and ("TTL=" in output):
                    pass
                # window get loss=0 when ping fail sometimes, need further check
                else:
                    test.fail(
                        "Guests ping test hit unexpected loss, error info: %s" % output
                    )

            # Let outer finally handle closing of sessions.
            return

        try:
            for vlan in range(1, vlan_num + 1):
                error_context.base_context("Test for vlan %s" % vlan, test.log.info)
                error_context.context("Ping test between vlans", test.log.info)
                interface = ifname[0] + "." + str(vlan)
                for vm_index in range(len(vms)):
                    for vlan2 in range(1, vlan_num + 1):
                        interface = ifname[vm_index] + "." + str(vlan)
                        dest = ".".join(
                            (subnet, str(vlan2), ip_unit[(vm_index + 1) % 2])
                        )
                        status, output = utils_test.ping(
                            dest,
                            count=2,
                            interface=interface,
                            session=sessions[vm_index],
                            timeout=30,
                        )
                        if (vlan == vlan2) ^ (status == 0):
                            err_msg = "%s ping %s unexpected, " % (interface, dest)
                            err_msg += "error info: %s" % output
                            test.fail(err_msg)

                error_context.context("Flood ping between vlans", test.log.info)
                vlan_ip[0] = ".".join((subnet, str(vlan), ip_unit[0]))
                vlan_ip[1] = ".".join((subnet, str(vlan), ip_unit[1]))
                flood_ping(0, 1)
                flood_ping(1, 0)

                error_context.context(
                    "Transferring data between vlans by nc", test.log.info
                )
                nc_transfer(test, 0, 1)
                nc_transfer(test, 1, 0)

        finally:
            # If client can not connect the nc server, need kill the server.
            for session in session_ctl:
                session.cmd_output_safe("killall -9 nc")
            error_context.base_context("Remove vlan")
            for vm_index in range(len(vms)):
                for vlan in range(1, vlan_num + 1):
                    status = rem_vlan(
                        test, sessions[vm_index], vlan, ifname[vm_index], cmd_type
                    )
                    if status:
                        test.log.error("Remove vlan %s failed", vlan)

        # Plumb/unplumb maximal number of vlan interfaces
        if params.get_boolean("do_maximal_test"):
            bound = maximal + 1
            vlan_added = 0
            try:
                error_context.base_context("Vlan scalability test")
                error_context.context(
                    "Testing the plumb of vlan interface", test.log.info
                )
                for vlan_index in range(1, bound):
                    if add_vlan(test, sessions[0], vlan_index, ifname[0], cmd_type):
                        vlan_added += 1
                    else:
                        test.log.info("Failed to add VLAN %s, stopping test", vlan_index)
                        break
                if vlan_added != maximal:
                    test.fail("Maximal interface plumb test failed, only added: %s" % vlan_added)
            finally:
                for vlan_index in range(1, vlan_added + 1):
                    if rem_vlan(test, sessions[0], vlan_index, ifname[0], cmd_type):
                        test.log.error("Remove vlan %s failed", vlan_index)

            error_context.base_context("Vlan negative test")
            error_context.context(
                "Create vlan with ID %s in guest" % bound, test.log.info
            )
            result = add_vlan(test, sessions[0], bound, ifname[0], cmd_type)
            if result:
                test.fail("VLAN ID %s should not be allowed (max is %s)" % (bound, maximal))
            else:
                test.log.info("Correctly failed to create VLAN with invalid ID %s", bound)
    finally:
        # Close all sessions opened during this test, regardless of outcome.
        for sess in sessions + session_ctl:
            if sess:
                sess.close()
