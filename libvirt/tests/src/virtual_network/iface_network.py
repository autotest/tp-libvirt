import re
import logging
from autotest.client.shared import error
from autotest.client import utils
from virttest import virt_vm, virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_libguestfs
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import NetworkXML


def run(test, params, env):
    """
    Test interafce xml options.

    1.Prepare test environment,destroy or suspend a VM.
    2.Edit xml and start the domain.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def prepare_pxe_boot():
        """
        Prepare tftp server and pxe boot files
        """
        pkg_list = ["syslinux", "tftp-server",
                    "tftp", "ipxe-roms-qemu", "wget"]
        # Try to install required packages
        if not utils_misc.yum_install(pkg_list):
            raise error.TestNAError("Failed ot install "
                                    "required packages")
        boot_initrd = params.get("boot_initrd")
        boot_vmlinuz = params.get("boot_vmlinuz")
        # Download pxe boot images
        utils.run("wget %s -O %s/initrd.img"
                  % (boot_initrd, tftp_root))
        utils.run("wget %s -O %s/vmlinuz"
                  % (boot_vmlinuz, tftp_root))
        utils.run("cp -f /usr/share/syslinux/pxelinux.0 {0};"
                  " mkdir -m 777 -p {0}/pxelinux.cfg".format(tftp_root))
        pxe_file = "%s/pxelinux.cfg/default" % tftp_root
        boot_txt = """
DISPLAY boot.txt
DEFAULT rhel
LABEL rhel
        kernel vmlinuz
        append initrd=initrd.img
PROMPT 1
TIMEOUT 3"""
        with open(pxe_file, 'w') as p_file:
            p_file.write(boot_txt)

    def modify_iface_xml():
        """
        Modify interface xml options
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if pxe_boot:
            # Config boot console for pxe boot
            osxml = vm_xml.VMOSXML()
            osxml.type = vmxml.os.type
            osxml.arch = vmxml.os.arch
            osxml.machine = vmxml.os.machine
            osxml.loader = "/usr/share/seabios/bios.bin"
            osxml.bios_useserial = "yes"
            osxml.bios_reboot_timeout = "-1"
            osxml.boots = ['network']
            del vmxml.os
            vmxml.os = osxml

        xml_devices = vmxml.devices
        iface_index = xml_devices.index(
            xml_devices.by_device_tag("interface")[0])
        iface = xml_devices[iface_index]
        iface_bandwidth = {}
        iface_inbound = eval(iface_bandwidth_inbound)
        iface_outbound = eval(iface_bandwidth_outbound)
        if iface_inbound:
            iface_bandwidth["inbound"] = iface_inbound
        if iface_outbound:
            iface_bandwidth["outbound"] = iface_outbound
        if iface_bandwidth:
            bandwidth = iface.new_bandwidth(**iface_bandwidth)
            iface.bandwidth = bandwidth

        iface_source = params.get("iface_source")
        if iface_source:
            source = eval(iface_source)
            if source:
                iface.source = source
        logging.debug("New interface xml file: %s", iface)
        vmxml.devices = xml_devices
        vmxml.xmltreefile.write()
        vmxml.sync()

    def run_dnsmasq_default_test(key, value=None, exists=True):
        """
        Test dnsmasq configuration.
        """
        conf_file = "/var/lib/libvirt/dnsmasq/default.conf"
        configs = ""
        with open(conf_file) as f:
            configs = f.read()
        logging.debug("configs in file %s: %s", conf_file, configs)
        if value:
            config = "%s=%s" % (key, value)
        else:
            config = key

        if not configs.count(config):
            if exists:
                raise error.TestFail("Can't find %s=%s in configuration"
                                     " file" % (key, value))
        else:
            if not exists:
                raise error.TestFail("Found %s=%s in configuration"
                                     " file" % (key, value))

    def run_dnsmasq_addnhosts_test(hostip, hostnames):
        """
        Test host ip and names configuration
        """
        conf_file = "/var/lib/libvirt/dnsmasq/default.addnhosts"
        hosts_re = ".*".join(hostnames)
        configs = ""
        with open(conf_file) as f:
            configs = f.read()
        logging.debug("configs in file %s: %s", conf_file, configs)
        if not re.search(r"%s.*%s" % (hostip, hosts_re), configs, re.M):
            raise error.TestFail("Can't find '%s' in configuration"
                                 " file" % hostip)

    def run_dnsmasq_host_test(iface_mac, guest_ip, guest_name):
        """
        Test host name and ip configuration for dnsmasq
        """
        conf_file = "/var/lib/libvirt/dnsmasq/default.hostsfile"
        config = "%s,%s,%s" % (iface_mac, guest_ip, guest_name)
        configs = ""
        with open(conf_file) as f:
            configs = f.read()
        logging.debug("configs in file %s: %s", conf_file, configs)
        if not configs.count(config):
            raise error.TestFail("Can't find host configuration"
                                 " in file %s" % conf_file)

    def check_class_rules(ifname, rule_id, bandwidth):
        """
        Check bandwidth settings via 'tc class' output
        """
        cmd = "tc class show dev %s" % ifname
        class_output = utils.run(cmd).stdout
        logging.debug("Bandwidth class output: %s", class_output)
        class_pattern = (r"class htb %s.*rate (\d+)Kbit ceil"
                         " (\d+)Kbit burst (\d+)(K?M?)b.*" % rule_id)
        se = re.search(class_pattern, class_output, re.M)
        if not se:
            raise error.TestFail("Can't find outbound setting"
                                 " for htb %s" % rule_id)
        logging.debug("bandwidth from tc output:%s" % str(se.groups()))
        ceil = None
        if bandwidth.has_key("floor"):
            ceil = int(bandwidth["floor"]) * 8
        elif bandwidth.has_key("average"):
            ceil = int(bandwidth["average"]) * 8
        if ceil:
            assert int(se.group(1)) == ceil
        if bandwidth.has_key("peak"):
            assert int(se.group(2)) == int(bandwidth["peak"]) * 8
        if bandwidth.has_key("burst"):
            if se.group(4) == 'M':
                tc_burst = int(se.group(3)) * 1024
            else:
                tc_burst = int(se.group(3))
            assert tc_burst == int(bandwidth["burst"])

    def check_filter_rules(ifname, bandwidth):
        """
        Check bandwidth settings via 'tc filter' output
        """
        cmd = "tc -d filter show dev %s parent ffff:" % ifname
        filter_output = utils.run(cmd).stdout
        logging.debug("Bandwidth filter output: %s", filter_output)
        if not filter_output.count("filter protocol all pref"):
            raise error.TestFail("Can't find 'protocol all' settings"
                                 " in filter rules")
        filter_pattern = ".*police.*rate (\d+)Kbit burst (\d+)Kb.*"
        se = re.search(r"%s" % filter_pattern, filter_output, re.M)
        if not se:
            raise error.TestFail("Can't find any filter policy")
        logging.debug("bandwidth from tc output:%s" % str(se.groups()))
        if bandwidth.has_key("average"):
            assert int(se.group(1)) == int(bandwidth["average"]) * 8
        if bandwidth.has_key("burst"):
            assert int(se.group(2)) == int(bandwidth["burst"])

    def run_bandwidth_test(check_net=False, check_iface=False):
        """
        Test bandwidth option for network or interface by tc command.
        """
        iface_inbound = eval(iface_bandwidth_inbound)
        iface_outbound = eval(iface_bandwidth_outbound)
        net_inbound = eval(net_bandwidth_inbound)
        net_outbound = eval(net_bandwidth_outbound)
        net_bridge_name = eval(net_bridge)["name"]
        iface_name = libvirt.get_ifname_host(vm_name, iface_mac)

        try:
            if check_net and net_inbound:
                # Check qdisc rules
                cmd = "tc -d qdisc show dev %s" % net_bridge_name
                qdisc_output = utils.run(cmd).stdout
                logging.debug("Bandwidth qdisc output: %s", qdisc_output)
                if not qdisc_output.count("qdisc ingress ffff:"):
                    raise error.TestFail("Can't find ingress setting")
                check_class_rules(net_bridge_name, "1:1",
                                  {"average": net_inbound["average"],
                                   "peak": net_inbound["peak"]})
                check_class_rules(net_bridge_name, "1:2", net_inbound)

            # Check filter rules on bridge interface
            if check_net and net_outbound:
                check_filter_rules(net_bridge_name, net_outbound)

            # Check class rules on interface inbound settings
            if check_iface and iface_inbound:
                check_class_rules(iface_name, "1:1",
                                  {'average': iface_inbound['average'],
                                   'peak': iface_inbound['peak'],
                                   'burst': iface_inbound['burst']})
                if iface_inbound.has_key("floor"):
                    check_class_rules(net_bridge_name, "1:3",
                                      {'floor': iface_inbound["floor"]})

            # Check filter rules on interface outbound settings
            if check_iface and iface_outbound:
                check_filter_rules(iface_name, iface_outbound)
        except AssertionError:
            utils.log_last_traceback()
            raise error.TestFail("Failed to check network bandwidth")

    def check_name_ip(session):
        """
        Check dns resolving on guest
        """
        # Check if bind-utils is installed
        if not utils_misc.yum_install(['bind-utils'], session):
            raise error.TestNAError("Failed to install bind-utils"
                                    " on guest")
        # Run host command to check if hostname can be resolved
        if not guest_ipv4 and not guest_ipv6:
            raise error.TestFail("No ip address found from parameters")
        guest_ip = guest_ipv4 if guest_ipv4 else guest_ipv6
        cmd = "host %s | grep %s" % (guest_name, guest_ip)
        if session.cmd_status(cmd):
            raise error.TestFail("Can't resolve name %s on guest" %
                                 guest_name)

    def check_ipt_rules(check_ipv4=True, check_ipv6=False):
        """
        Check iptables for network/interface
        """
        br_name = eval(net_bridge)["name"]
        net_forward = eval(params.get("net_forward", "{}"))
        net_ipv4 = params.get("net_ipv4")
        net_ipv6 = params.get("net_ipv6")
        ipt_rules = ("FORWARD -i {0} -o {0} -j ACCEPT".format(br_name),
                     "FORWARD -o %s -j REJECT --reject-with icmp" % br_name,
                     "FORWARD -i %s -j REJECT --reject-with icmp" % br_name)
        net_dev_in = ""
        net_dev_out = ""
        if net_forward.has_key("dev"):
            net_dev_in = " -i %s" % net_forward["dev"]
            net_dev_out = " -o %s" % net_forward["dev"]
        if check_ipv4:
            ipv4_rules = list(ipt_rules)
            ctr_rule = ""
            nat_rules = []
            if net_forward.has_key("mode") and net_forward["mode"] == "nat":
                nat_port = eval(params.get("nat_port"))
                p_start = nat_port["start"]
                p_end = nat_port["end"]
                ctr_rule = " -m conntrack --ctstate RELATED,ESTABLISHED"
                nat_rules = ["POSTROUTING -s %s -d 224.0.0.0/24 -j RETURN" % net_ipv4,
                             "POSTROUTING -s %s -d 255.255.255.255/32 -j RETURN" % net_ipv4,
                             ("POSTROUTING -s {0} ! -d {0} -p tcp -j MASQUERADE"
                              " --to-ports {1}-{2}".format(net_ipv4, p_start, p_end)),
                             ("POSTROUTING -s {0} ! -d {0} -p udp -j MASQUERADE"
                              " --to-ports {1}-{2}".format(net_ipv4, p_start, p_end)),
                             ("POSTROUTING -s {0} ! -d {0} -p udp"
                              " -j MASQUERADE".format(net_ipv4))]
            if nat_rules:
                ipv4_rules.extend(nat_rules)
            if (net_ipv4 and net_forward.has_key("mode") and
                    net_forward["mode"] in ["nat", "route"]):
                rules = [("FORWARD -d %s%s -o %s%s -j ACCEPT"
                          % (net_ipv4, net_dev_in, br_name, ctr_rule)),
                         ("FORWARD -s %s -i %s%s -j ACCEPT"
                          % (net_ipv4, br_name, net_dev_out))]
                ipv4_rules.extend(rules)

            output = utils.run("iptables-save").stdout.strip()
            logging.debug("iptables: %s", output)
            for ipt in ipv4_rules:
                if not output.count(ipt):
                    raise error.TestFail("Can't find iptable rule:\n%s" % ipt)
        if check_ipv6:
            ipv6_rules = list(ipt_rules)
            if (net_ipv6 and net_forward.has_key("mode") and
                    net_forward["mode"] in ["nat", "route"]):
                rules = [("FORWARD -d %s%s -o %s -j ACCEPT"
                          % (net_ipv6, net_dev_in, br_name)),
                         ("FORWARD -s %s -i %s%s -j ACCEPT"
                          % (net_ipv6, br_name, net_dev_out))]
                ipv6_rules.extend(rules)
            output = utils.run("ip6tables-save").stdout.strip()
            logging.debug("iptables: %s", output)
            for ipt in ipv6_rules:
                if not output.count(ipt):
                    raise error.TestFail("Can't find ipbtable rule:\n%s" % ipt)

    def run_ip_test(session, ip_ver):
        """
        Check iptables on host and ipv6 address on guest
        """
        if ip_ver == "ipv6":
            # Clean up iptables rules for guest to get ipv6 address
            session.cmd_status("ip6tables -F")
        utils_net.restart_guest_network(session, iface_mac,
                                        ip_version=ip_ver)
        # It may take some time to get the ip address
        get_ip_func = lambda: utils_net.get_guest_ip_addr(session, iface_mac,
                                                          ip_version=ip_ver)
        utils_misc.wait_for(get_ip_func, 10)
        vm_ip = get_ip_func()
        logging.debug("Guest has ip: %s", vm_ip)
        if not vm_ip:
            raise error.TestFail("Can't find ip address on guest")
        ping_cmd = "ping -c 5"
        ip_gateway = net_ip_address
        if ip_ver == "ipv6":
            ping_cmd = "ping6 -c 5"
            ip_gateway = net_ipv6_address
        if ip_gateway:
            if utils.system("%s %s" % (ping_cmd, ip_gateway),
                            ignore_status=True):
                raise error.TestFail("Failed to ping gateway address: %s"
                                     % ip_gateway)

    def run_guest_libvirt(session):
        """
        Check guest libvirt network
        """
        # Try to install required packages
        if not utils_misc.yum_install(['libvirt'], session):
            raise error.TestNAError("Failed ot install libvirt"
                                    " package on guest")
        result = True
        # Check network state on guest
        cmd = ("service libvirtd restart; virsh net-info default"
               " | grep 'Active:.*no'")
        if session.cmd_status(cmd):
            result = False
            logging.error("Default network isn't in inactive state")
        # Try to start default network on guest, check error messages
        if result:
            cmd = "virsh net-start default"
            status, output = session.cmd_status_output(cmd)
            logging.debug("Run command on guest exit %s, output %s"
                          % (status, output))
            if not status or not output.count("already in use"):
                result = False
                logging.error("Failed to see network messges on guest")
        if session.cmd_status("rpm -e libvirt"):
            logging.error("Failed to remove libvirt packages on guest")

        if not result:
            raise error.TestFail("Check libvirt network on guest failed")

    start_error = "yes" == params.get("start_error", "no")
    restart_error = "yes" == params.get("restart_error", "no")

    # network specific attributes.
    net_name = params.get("net_name", "default")
    net_bridge = params.get("net_bridge", "{'name':'virbr0'}")
    net_domain = params.get("net_domain")
    net_ip_address = params.get("net_ip_address")
    net_ipv6_address = params.get("net_ipv6_address")
    net_dns_forward = params.get("net_dns_forward")
    net_dns_txt = params.get("net_dns_txt")
    net_dns_srv = params.get("net_dns_srv")
    net_dns_hostip = params.get("net_dns_hostip")
    net_dns_hostnames = params.get("net_dns_hostnames", "").split()
    dhcp_start_ipv4 = params.get("dhcp_start_ipv4")
    dhcp_end_ipv4 = params.get("dhcp_end_ipv4")
    guest_name = params.get("guest_name")
    guest_ipv4 = params.get("guest_ipv4")
    guest_ipv6 = params.get("guest_ipv6")
    tftp_root = params.get("tftp_root")
    pxe_boot = "yes" == params.get("pxe_boot", "no")
    net_bandwidth_inbound = params.get("net_bandwidth_inbound", "{}")
    net_bandwidth_outbound = params.get("net_bandwidth_outbound", "{}")
    iface_bandwidth_inbound = params.get("iface_bandwidth_inbound", "{}")
    iface_bandwidth_outbound = params.get("iface_bandwidth_outbound", "{}")
    multiple_guests = params.get("multiple_guests")
    create_network = "yes" == params.get("create_network", "no")
    serial_login = "yes" == params.get("serial_login", "no")
    change_iface_option = "yes" == params.get("change_iface_option", "no")
    test_bridge = "yes" == params.get("test_bridge", "no")
    test_dnsmasq = "yes" == params.get("test_dnsmasq", "no")
    test_dhcp_range = "yes" == params.get("test_dhcp_range", "no")
    test_dns_host = "yes" == params.get("test_dns_host", "no")
    test_qos_bandwidth = "yes" == params.get("test_qos_bandwidth", "no")
    test_qos_remove = "yes" == params.get("test_qos_remove", "no")
    test_ipv4_address = "yes" == params.get("test_ipv4_address", "no")
    test_ipv6_address = "yes" == params.get("test_ipv6_address", "no")
    test_guest_libvirt = "yes" == params.get("test_guest_libvirt", "no")

    if serial_login:
        # Set serial console for serial login
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()
        # Set console option
        vm.set_kernel_console("ttyS0", "115200")
        # Shutdown here for sync fs
        vm.shutdown()
    else:
        if vm.is_alive():
            vm.destroy(gracefully=False)

    # Back up xml file.
    netxml_backup = NetworkXML.new_from_net_dumpxml("default")
    iface_mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)
    params["guest_mac"] = iface_mac
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vms_list = []

    # Build the xml and run test.
    try:
        if test_dnsmasq:
            # Check the settings before modifying network xml
            if net_dns_forward == "no":
                run_dnsmasq_default_test("domain-needed", exists=False)
                run_dnsmasq_default_test("local", "//", exists=False)
            if net_domain:
                run_dnsmasq_default_test("domain", net_domain, exists=False)
                run_dnsmasq_default_test("expand-hosts", exists=False)

        # Prepare pxe boot directory
        if pxe_boot:
            prepare_pxe_boot()
        # Edit the network xml or create a new one.
        if create_network:
            libvirt.create_net_xml(net_name, params)
        # Edit the interface xml.
        if change_iface_option:
            modify_iface_xml()

        if multiple_guests:
            # Clone more vms for testing
            for i in range(int(multiple_guests)):
                guest_name = "%s_%s" % (vm_name, i)
                utils_libguestfs.virt_clone_cmd(vm_name, guest_name, True)
                vms_list.append(vm.clone(guest_name))

        if test_bridge:
            bridge = eval(net_bridge)
            br_if = utils_net.Interface(bridge['name'])
            if not br_if.is_up():
                raise error.TestFail("Bridge interface isn't up")
        if test_dnsmasq:
            # Check the settings in dnsmasq config file
            if net_dns_forward == "no":
                run_dnsmasq_default_test("domain-needed")
                run_dnsmasq_default_test("local", "//")
            if net_domain:
                run_dnsmasq_default_test("domain", net_domain)
                run_dnsmasq_default_test("expand-hosts")
            if net_bridge:
                bridge = eval(net_bridge)
                run_dnsmasq_default_test("interface", bridge['name'])
                if bridge.has_key('stp') and bridge['stp'] == 'on':
                    if bridge.has_key('delay'):
                        br_delay = float(bridge['delay'])
                        cmd = ("brctl showstp %s | grep 'bridge forward delay'"
                               % bridge['name'])
                        out = utils.run(cmd, ignore_status=False).stdout.strip()
                        logging.debug("brctl showstp output: %s", out)
                        pattern = (r"\s*forward delay\s+(\d+.\d+)\s+bridge"
                                   " forward delay\s+(\d+.\d+)")
                        match_obj = re.search(pattern, out, re.M)
                        if not match_obj or len(match_obj.groups()) != 2:
                            raise error.TestFail("Can't see forward delay"
                                                 " messages from command")
                        elif (float(match_obj.groups()[0]) != br_delay or
                                float(match_obj.groups()[1]) != br_delay):
                            raise error.TestFail("Foward delay setting"
                                                 " can't take effect")
            if dhcp_start_ipv4 and dhcp_end_ipv4:
                run_dnsmasq_default_test("dhcp-range", "%s,%s"
                                         % (dhcp_start_ipv4, dhcp_end_ipv4))
            if guest_name and guest_ipv4:
                run_dnsmasq_host_test(iface_mac, guest_ipv4, guest_name)

        if test_dns_host:
            if net_dns_txt:
                dns_txt = eval(net_dns_txt)
                run_dnsmasq_default_test("txt-record", "%s,%s" %
                                         (dns_txt["name"],
                                          dns_txt["value"]))
            if net_dns_srv:
                dns_srv = eval(net_dns_srv)
                run_dnsmasq_default_test("srv-host", "_%s._%s.%s,%s,%s,%s,%s" %
                                         (dns_srv["service"], dns_srv["protocol"],
                                          dns_srv["domain"], dns_srv["target"],
                                          dns_srv["port"], dns_srv["priority"],
                                          dns_srv["weight"]))
            if net_dns_hostip and net_dns_hostnames:
                run_dnsmasq_addnhosts_test(net_dns_hostip, net_dns_hostnames)

        # Run bandwidth test for network
        if test_qos_bandwidth:
            run_bandwidth_test(check_net=True)

        try:
            # Start the VM.
            vm.start()
            if start_error:
                raise error.TestFail("VM started unexpectedly")
            if pxe_boot:
                # Just check network boot messages here
                vm.serial_console.read_until_output_matches(
                    ["Loading vmlinuz", "Loading initrd.img"],
                    utils_misc.strip_console_codes)
                output = vm.serial_console.get_stripped_output()
                logging.debug("Boot messages: %s", output)

            else:
                if serial_login:
                    session = vm.wait_for_serial_login()
                else:
                    session = vm.wait_for_login()

                if test_dhcp_range:
                    # First vm should have a valid ip address
                    utils_net.restart_guest_network(session, iface_mac)
                    vm_ip = utils_net.get_guest_ip_addr(session, iface_mac)
                    logging.debug("Guest has ip: %s", vm_ip)
                    if not vm_ip:
                        raise error.TestFail("Guest has invalid ip address")
                    # Other vms cloudn't get the ip address
                    for vms in vms_list:
                        # Start other VMs.
                        vms.start()
                        sess = vms.wait_for_serial_login()
                        vms_mac = vms.get_virsh_mac_address()
                        # restart guest network to get ip addr
                        utils_net.restart_guest_network(sess, vms_mac)
                        vms_ip = utils_net.get_guest_ip_addr(sess,
                                                             vms_mac)
                        if vms_ip:
                            # Get IP address on guest should return Null
                            raise error.TestFail("Guest has ip address: %s"
                                                 % vms_ip)
                        sess.close()

                # Check dnsmasq settings if take affect in guest
                if guest_ipv4:
                    check_name_ip(session)

                # Run bandwidth test for interface
                if test_qos_bandwidth:
                    run_bandwidth_test(check_iface=True)
                if test_qos_remove:
                    # Remove the bandwidth settings in network xml
                    logging.debug("Removing network bandwidth settings...")
                    netxml_backup.sync()
                    vm.destroy(gracefully=False)
                    # Should fail to start vm
                    vm.start()
                    if restart_error:
                        raise error.TestFail("VM started unexpectedly")
                if test_ipv4_address:
                    check_ipt_rules(check_ipv4=True)
                    run_ip_test(session, "ipv4")
                if test_ipv6_address:
                    check_ipt_rules(check_ipv6=True)
                    run_ip_test(session, "ipv6")

                if test_guest_libvirt:
                    run_guest_libvirt(session)

                session.close()
        except virt_vm.VMStartError, details:
            logging.info(str(details))
            if start_error or restart_error:
                pass
            else:
                raise error.TestFail('VM Failed to start for some reason!')

    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        for vms in vms_list:
            virsh.remove_domain(vms.name, "--remove-all-storage")
        logging.info("Restoring network...")
        if net_name == "default":
            netxml_backup.sync()
        else:
            # Destroy and undefine new created network
            virsh.net_destroy(net_name)
            virsh.net_undefine(net_name)
        vmxml_backup.sync()
