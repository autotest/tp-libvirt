# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os
import time

from avocado.utils import crypto
from avocado.utils import process

from virttest import data_dir
from virttest import remote
from virttest import utils_net
from virttest import utils_test
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Test basic QinQ - 10 * 4096 with bridge backend using libvirt

    1) Create a private bridge using libvirt network
    2) Boot a VM over private network
    3) Create interfaces in guest with qinq.sh
    4) Set IP on guest L1 interface and bring this interface on
    5) Create 802.1ad interface on host with the private bridge
    6) Start tcpdump on host
    7) Do ping test
    8) Check tcpdump result with vlan tag and ethertype
    9) Set IP on guest L2 interface and bring this interface on
    10) Create 802.1q interface on host with the 802.1ad interface
    11) Start tcpdump on host
    12) Do ping test
    13) Check tcpdump result with vlan tag and ethertype
    14) SCP file transfer between host and guest

    :param test: libvirt test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def check_tcpdump_result(
        session,
        iface_name,
        ethertype,
        ethertype2=None,
        vlan_tag=None,
        vlan_tag2=None,
        enable_logging=False,
    ):
        """
        Check tcpdump result.

        :param session: guest session
        :param iface_name: the tcpdump file of the interface
        :param ethertype: ethertype value need to be matched
        :param ethertype2: ethertype value 2 needed to be matched if not None
        :param vlan_tag: vlan tag value needed to be matched if not None
        :param vlan_tag2: vlan tag value 2 needed to be matched if not None
        :param enable_logging: whether to dump tcpdump results during test
        """
        get_tcpdump_log_cmd = params.get("get_tcpdump_log_cmd") % iface_name
        lines = session.cmd_output(
            get_tcpdump_log_cmd, timeout=300, safe=True).strip().splitlines()
        sum = 0
        for i in range(1, len(lines)):
            if enable_logging:
                test.log.info("line %s: %s", i, lines[i - 1])
            if not ethertype2:
                if "ICMP echo re" in lines[i] and ethertype in lines[i - 1]:
                    sum += 1
                    if vlan_tag and vlan_tag not in lines[i - 1]:
                        if "too much work for irq" in lines[i - 1]:
                            continue
                        else:
                            test.fail(
                                "in %s tcpdump log, there should be vlan "
                                "tag %s" % (iface_name, vlan_tag)
                            )
                    elif not vlan_tag:
                        if "vlan" in lines[i - 1]:
                            test.fail(
                                "in %s tcpdump log, there should not be "
                                "vlan tag" % iface_name
                            )
            else:
                if "ICMP echo re" in lines[i] \
                   and ethertype in lines[i - 1] \
                   and ethertype2 in lines[i - 1]:
                    sum += 1
                    if vlan_tag not in lines[i - 1] or vlan_tag2 not in lines[i - 1]:
                        if "too much work for irq" in lines[i - 1]:
                            continue
                        else:
                            test.fail(
                                "in %s tcpdump log, there should be vlan "
                                "tag %s" % (iface_name, vlan_tag)
                            )
        if sum == 0:
            test.fail(
                "in %s tcpdump log, ethertype is not %s" % (iface_name, ethertype)
            )
        test.log.info("tcpdump result check passed for %s - found %d ICMP echo replies with expected ethertype %s%s%s",
                      iface_name, sum, ethertype,
                      f" and {ethertype2}" if ethertype2 else "",
                      f" with vlan tags {vlan_tag}{vlan_tag2 if vlan_tag2 else ''}" if vlan_tag else "")

    def compare_host_guest_md5sum():
        """
        Compare md5 value of file on host and guest
        """
        test.log.info("Comparing md5sum on guest and host")
        host_result = crypto.hash_file(host_path, algorithm="md5")
        guest_result = session.cmd_output("md5sum %s" % guest_path, 120).split()[0]
        test.log.debug("md5sum: guest(%s), host(%s)", guest_result, host_result)

        return guest_result == host_result

    vm_name = params.get('main_vm')
    qvlan_ifname = params.get("qvlan_name", "")
    add_qvlan_cmd = params.get("add_qvlan_cmd", "")
    username = params.get("username")
    password = params.get("password")

    if params.get("netdst") not in utils_net.Bridge().list_br():
        test.cancel("Only support Linux bridge")
    guest_qinq_dir = params.get("guest_qinq_dir")
    host_qinq_dir = os.path.join(data_dir.get_deps_dir(), params.get("qin_script"))

    brname = params.get("private_bridge", "tmpbr")
    set_ip_cmd = params.get("set_ip_cmd")
    file_size = params.get_numeric("file_size", "4096")
    host_path = os.path.join(test.tmpdir, "transferred_file")
    guest_path = params.get("guest_path", "/var/tmp/transferred_file")
    transfer_timeout = params.get_numeric("transfer_timeout", 1000)
    login_timeout = params.get_numeric("login_timeout", "600")
    iface_dict = eval(params.get("iface_dict"))

    try:
        vm = env.get_vm(params.get("main_vm"))
        vm.verify_alive()
        session = vm.wait_for_login(timeout=login_timeout)
        test.log.debug("STEP1: Copy qinq script to guest")
        vm.copy_files_to(host_qinq_dir, guest_qinq_dir)
        session.close()

        test.log.debug("STEP2: Destroy vm ")
        vm.destroy(gracefully=True)

        test.log.debug("STEP3: Create private bridge %s", brname)
        host_bridges = utils_net.Bridge()
        if brname in host_bridges.list_br():
            utils_net.Interface(brname).down()
            host_bridges.del_bridge(brname)

        host_bridges.add_bridge(brname)
        host_bridge_iface = utils_net.Interface(brname)
        test.log.debug("Bring up %s", brname)
        process.system(set_ip_cmd % ("192.168.1.1", brname))
        host_bridge_iface.up()

        test.log.debug("STEP4: Prepare vm with new created private bridge %s", brname)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_dict)
        vm.start()
        test.log.debug("Guest with new bridge xml: %s" % vm_xml.VMXML.new_from_dumpxml(vm_name))

        session = vm.wait_for_serial_login(timeout=login_timeout)
        stop_NM_cmd = params.get("stop_NM_cmd")
        session.cmd(stop_NM_cmd, ignore_all_errors=True)
        mac = vm.get_mac_address()
        nic_name = utils_net.get_linux_ifname(session, mac)

        test.log.debug("STEP5: Starting qin related test")
        # Set first_nic IP in guest
        ip = params.get("ip_vm")
        session.cmd_output(set_ip_cmd % (ip, nic_name))

        # Create vlans via script qinq.sh
        output = session.cmd_output(
            "sh %sqinq.sh %s" % (guest_qinq_dir, nic_name), timeout=300
        )
        test.log.info("%s", output)

        # Set interface v1v10 IP in guest
        L1tag_iface = params.get("L1tag_iface")
        L1tag_iface_ip = params.get("L1tag_iface_ip")
        session.cmd_output(set_ip_cmd % (L1tag_iface_ip, L1tag_iface))
        session.cmd("ip link set %s up" % L1tag_iface)
        output = session.cmd_output("ip addr show %s" % L1tag_iface, timeout=120)
        test.log.info(output)

        # Start tcpdump on L1tag interface and first_nic in guest
        test.log.info("Start tcpdump in %s", vm_name)
        L1tag_tcpdump_log = params.get("tcpdump_log") % L1tag_iface
        L1tag_tcpdump_cmd = params.get("tcpdump_cmd") % (L1tag_iface, L1tag_tcpdump_log)
        first_nic_tcpdump_log = params.get("tcpdump_log") % nic_name
        first_nic_tcpdump_cmd = params.get("tcpdump_cmd") % (
            nic_name,
            first_nic_tcpdump_log,
        )
        session.sendline(L1tag_tcpdump_cmd)
        time.sleep(2)
        session.sendline(first_nic_tcpdump_cmd)
        time.sleep(5)

        # Create 802.1ad vlan via bridge in host
        test.log.info("Create 802.1ad vlan via bridge %s", brname)
        advlan_ifname = params.get("advlan_name")
        add_advlan_cmd = params.get("add_advlan_cmd")
        process.run(add_advlan_cmd)
        advlan_iface = utils_net.Interface(advlan_ifname)
        advlan_iface.set_mac(params.get("advlan_mac"))
        process.run(set_ip_cmd % (params.get("advlan_ip"), advlan_ifname))
        advlan_iface.up()
        output = process.getoutput("ip addr show %s" % advlan_ifname)
        test.log.info(output)

        # Ping guest from host via 802.1ad vlan interface
        test.log.info("Start ping test from host to %s via %s", L1tag_iface_ip, advlan_ifname)
        ping_count = params.get_numeric("ping_count")
        status, output = utils_net.ping(
            L1tag_iface_ip,
            ping_count,
            interface=advlan_ifname,
            timeout=ping_count * 1.5,
        )
        if status != 0:
            test.fail("Ping returns non-zero value %s" % output)
        package_lost = utils_test.get_loss_ratio(output)
        if package_lost != 0:
            test.fail(
                "%s package lost when ping guest ip %s "
                % (package_lost, L1tag_iface_ip)
            )

        # Stop tcpdump and check result
        session.cmd_output_safe("pkill tcpdump")
        check_tcpdump_result(session, L1tag_iface, "ethertype IPv4 (0x0800)")
        check_tcpdump_result(
            session, nic_name, "ethertype 802.1Q-QinQ (0x88a8)", vlan_tag="vlan 10,"
        )

        # Set IP on L2 tag on the guest interface with vid 20
        L2tag_iface = params.get("L2tag_iface")
        L2tag_iface_ip = params.get("L2tag_iface_ip")
        session.cmd_output(set_ip_cmd % (L2tag_iface_ip, L2tag_iface))
        session.cmd("ip link set %s up" % L2tag_iface)
        output = session.cmd_output("ip addr show %s" % L2tag_iface, timeout=120)
        test.log.info(output)

        # Start tcpdump on L1tag and L2tag interfaces and first_nic in guest
        test.log.info("Start tcpdump in %s", vm_name)
        L2tag_tcpdump_log = params.get("tcpdump_log") % L2tag_iface
        L2tag_tcpdump_cmd = params.get("tcpdump_cmd") % (L2tag_iface, L2tag_tcpdump_log)
        session.sendline(L1tag_tcpdump_cmd)
        time.sleep(2)
        session.sendline(L2tag_tcpdump_cmd)
        time.sleep(2)
        session.sendline(first_nic_tcpdump_cmd)
        time.sleep(5)

        # Create 802.1q vlan via 802.1ad vlan in host
        test.log.info("Create 802.1q vlan via 802.1ad vlan %s", advlan_ifname)
        process.system_output(add_qvlan_cmd)
        qvlan_iface = utils_net.Interface(qvlan_ifname)
        process.system(set_ip_cmd % (params.get("qvlan_ip"), qvlan_ifname))
        qvlan_iface.up()
        output = process.getoutput("ip addr show %s" % qvlan_ifname)
        test.log.info(output)

        # Ping guest from host via 802.1q vlan interface
        test.log.info("Start ping test from host to %s via %s", L2tag_iface_ip, qvlan_ifname)
        status, output = utils_net.ping(
            L2tag_iface_ip,
            ping_count,
            interface=qvlan_ifname,
            timeout=ping_count * 1.5,
        )
        if status != 0:
            test.fail("Ping returns non-zero value %s" % output)
        package_lost = utils_test.get_loss_ratio(output)
        if package_lost >= 5:
            test.fail(
                "%s packeage lost when ping guest ip %s "
                % (package_lost, L2tag_iface_ip)
            )

        # Stop tcpdump and check result
        session.cmd_output_safe("pkill tcpdump")
        check_tcpdump_result(
            session, L1tag_iface, "ethertype 802.1Q (0x8100)", vlan_tag="vlan 20,"
        )
        check_tcpdump_result(session, L2tag_iface, "ethertype IPv4 (0x0800)")
        check_tcpdump_result(
            session,
            nic_name,
            ethertype="ethertype 802.1Q-QinQ (0x88a8)",
            ethertype2="ethertype 802.1Q",
            vlan_tag="vlan 10,",
            vlan_tag2="vlan 20,",
        )

        if utils_misc.compare_qemu_version(8, 1, 0) and \
                params.get("nic_model") == "e1000e":
            session.cmd("ip link set %s mtu 1504" % nic_name)

        # scp file to guest with L2 vlan tag
        cmd = "dd if=/dev/zero of=%s bs=1M count=%d" % (host_path, file_size)
        test.log.info("Creating %dMB file on host", file_size)
        process.run(cmd)
        test.log.info("Transferring file host -> guest, timeout: %ss", transfer_timeout)
        shell_port = params.get("shell_port", 22)

        remote.scp_to_remote(
            L2tag_iface_ip, shell_port, username, password, host_path, guest_path
        )
        if not compare_host_guest_md5sum():
            test.fail("md5sum mismatch on guest and host")

    finally:
        session.cmd("rm -rf %s" % guest_path)
        session.close()
        virsh.destroy(vm_name)
        host_bridge_iface.down()
        host_bridges.del_bridge(brname)
