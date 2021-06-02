import re
import os
import sys
import ast
import logging
import platform

from avocado.utils import process
from avocado.utils import stacktrace

from aexpect.exceptions import ExpectTimeoutError

from virttest import virt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_package
from virttest import utils_libguestfs
from virttest import utils_libvirtd
from virttest.utils_test import libvirt
from virttest.utils_test.__init__ import ping
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.libvirt_xml.devices.interface import Interface
from virttest import libvirt_version


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
    host_arch = platform.machine()

    def prepare_pxe_boot():
        """
        Prepare tftp server and pxe boot files
        """
        pkg_list = ["syslinux", "tftp-server",
                    "tftp", "ipxe-roms-qemu", "wget"]
        # Try to install required packages
        if not utils_package.package_install(pkg_list):
            test.error("Failed ot install required packages")
        boot_initrd = params.get("boot_initrd", "EXAMPLE_INITRD")
        boot_vmlinuz = params.get("boot_vmlinuz", "EXAMPLE_VMLINUZ")
        if boot_initrd.count("EXAMPLE") or boot_vmlinuz.count("EXAMPLE"):
            test.cancel("Please provide initrd/vmlinuz URL")
        # Download pxe boot images
        process.system("wget %s -O %s/initrd.img" % (boot_initrd, tftp_root))
        process.system("wget %s -O %s/vmlinuz" % (boot_vmlinuz, tftp_root))
        process.system("cp -f /usr/share/syslinux/pxelinux.0 {0};"
                       " mkdir -m 777 -p {0}/pxelinux.cfg".format(tftp_root), shell=True)
        ldlinux_file = "/usr/share/syslinux/ldlinux.c32"
        if os.path.exists(ldlinux_file):
            process.system("cp -f {0} {1}".format(ldlinux_file, tftp_root), shell=True)
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

    def modify_iface_xml(sync=True):
        """
        Modify interface xml options

        :param sync: whether or not sync vmxml after the iface xml modified,
                    default to be True
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
            if utils_misc.compare_qemu_version(4, 0, 0, False):
                osxml.bios_reboot_timeout = "-1"
            osxml.boots = ['network']
            del vmxml.os
            vmxml.os = osxml

        xml_devices = vmxml.devices
        iface_index = xml_devices.index(
            xml_devices.by_device_tag("interface")[0])
        iface = xml_devices[iface_index]
        if not sync:
            params.setdefault('original_iface', vmxml.devices[iface_index])
        iface_bandwidth = {}
        iface_inbound = ast.literal_eval(iface_bandwidth_inbound)
        iface_outbound = ast.literal_eval(iface_bandwidth_outbound)
        if iface_inbound:
            iface_bandwidth["inbound"] = iface_inbound
        if iface_outbound:
            iface_bandwidth["outbound"] = iface_outbound
        if iface_bandwidth:
            bandwidth = iface.new_bandwidth(**iface_bandwidth)
            iface.bandwidth = bandwidth

        iface_type = params.get("iface_type", "network")
        iface.type_name = iface_type
        source = ast.literal_eval(iface_source)
        if not source:
            source = {"network": "default"}
        net_ifs = utils_net.get_net_if(state="UP")
        # Check source device is valid or not,
        # if it's not in host interface list, try to set
        # source device to first active interface of host
        if (iface.type_name == "direct" and
                'dev' in source and
                source['dev'] not in net_ifs):
            logging.warning("Source device %s is not a interface of host, reset to %s",
                            source['dev'], net_ifs[0])
            source['dev'] = net_ifs[0]
        del iface.source
        iface.source = source
        if iface_model:
            iface.model = get_iface_model(iface_model, host_arch)
        if iface_rom:
            iface.rom = eval(iface_rom)
        if iface_boot:
            vmxml.remove_all_boots()
            iface.boot = iface_boot
        if iface_driver:
            driver_dict = ast.literal_eval(iface_driver)
            iface.driver = iface.new_driver(driver_attr=driver_dict)
        logging.debug("New interface xml file: %s", iface)
        if sync:
            vmxml.devices = xml_devices
            vmxml.xmltreefile.write()
            vmxml.sync()
        else:
            return iface

    def get_iface_model(iface_model, host_arch):
        """
        Return iface_model. In case of s390x modify iface_model and log the change.
        :param iface_model: iface_model from params
        :param host_arch: host architecture, e.g. s390x
        :return: //interface/model@type
        """
        if 's390x' == host_arch:
            logging.debug("On s390x only valid model type are the virtio(-*)."
                          " Using virtio and ignoring config value %s" % iface_model)
            return "virtio"
        else:
            return iface_model

    def run_dnsmasq_default_test(key, value=None, exists=True, name="default"):
        """
        Test dnsmasq configuration.

        :param key: key in conf file to check
        :param value: value in conf file to check
        :param exists: check the key:value exist or not
        :param name: The name of conf file
        """
        conf_file = "/var/lib/libvirt/dnsmasq/%s.conf" % name
        if not os.path.exists(conf_file):
            test.cancel("Can't find %s.conf file" % name)

        configs = ""
        with open(conf_file, 'r') as f:
            configs = f.read()
        logging.debug("configs in file %s: %s", conf_file, configs)
        if value:
            config = "%s=%s" % (key, value)
        else:
            config = key

        if not configs.count(config):
            if exists:
                test.fail("Can't find %s=%s in configuration file" % (key, value))
        else:
            if not exists:
                test.fail("Found %s=%s in configuration file" % (key, value))

    def run_dnsmasq_addnhosts_test(hostip, hostnames):
        """
        Test host ip and names configuration
        """
        conf_file = "/var/lib/libvirt/dnsmasq/default.addnhosts"
        hosts_re = ".*".join(hostnames)
        configs = ""
        with open(conf_file, 'r') as f:
            configs = f.read()
        logging.debug("configs in file %s: %s", conf_file, configs)
        if not re.search(r"%s.*%s" % (hostip, hosts_re), configs, re.M):
            test.fail("Can't find '%s' in configuration file" % hostip)

    def run_dnsmasq_host_test(iface_mac, guest_ip, guest_name):
        """
        Test host name and ip configuration for dnsmasq
        """
        conf_file = "/var/lib/libvirt/dnsmasq/default.hostsfile"
        config = "%s,%s,%s" % (iface_mac, guest_ip, guest_name)
        configs = ""
        with open(conf_file, 'r') as f:
            configs = f.read()
        logging.debug("configs in file %s: %s", conf_file, configs)
        if not configs.count(config):
            test.fail("Can't find host configuration in file %s" % conf_file)

    def check_class_rules(ifname, rule_id, bandwidth):
        """
        Check bandwidth settings via 'tc class' output
        """
        cmd = "tc class show dev %s" % ifname
        class_output = process.run(cmd, shell=True).stdout_text
        logging.debug("Bandwidth class output: %s", class_output)
        class_pattern = (r"class htb %s.*rate (\d+)(K?M?)bit ceil (\d+)(K?M?)bit burst (\d+)(K?M?)b.*" % rule_id)
        se = re.search(class_pattern, class_output, re.M)
        if not se:
            test.fail("Can't find outbound setting for htb %s" % rule_id)
        logging.debug("bandwidth from tc output:%s" % str(se.groups()))
        rate = None
        if "floor" in bandwidth:
            rate = int(bandwidth["floor"]) * 8
        elif "average" in bandwidth:
            rate = int(bandwidth["average"]) * 8
        if rate:
            if se.group(2) == 'M':
                rate_check = int(se.group(1)) * 1000
            else:
                rate_check = int(se.group(1))
            assert rate_check == rate
        if "peak" in bandwidth:
            if se.group(4) == 'M':
                ceil_check = int(se.group(3)) * 1000
            else:
                ceil_check = int(se.group(3))
            assert ceil_check == int(bandwidth["peak"]) * 8
        if "burst" in bandwidth:
            if se.group(6) == 'M':
                tc_burst = int(se.group(5)) * 1024
            else:
                tc_burst = int(se.group(5))
            assert tc_burst == int(bandwidth["burst"])

    def check_filter_rules(ifname, bandwidth, expect_none=False):
        """
        Check bandwidth settings via 'tc filter' output

        :param ifname: name of iface to be checked
        :param bandwidth: bandwidth to be match with
        :param expect_none: whether or not expect nothing in output,
                            default to be False
        :return: if expect nothing from the output,
                            return True if the output is empty,
                            else return False
        """
        cmd = "tc -d filter show dev %s parent ffff:" % ifname
        filter_output = process.run(cmd, shell=True).stdout_text
        logging.debug("Bandwidth filter output: %s", filter_output)
        if expect_none:
            return not filter_output.strip()
        if not filter_output.count("filter protocol all pref"):
            test.fail("Can't find 'protocol all' settings in filter rules")
        filter_pattern = r".*police.*rate (\d+)(K?M?)bit burst (\d+)(K?M?)b.*"
        se = re.search(r"%s" % filter_pattern, filter_output, re.M)
        if not se:
            test.fail("Can't find any filter policy")
        logging.debug("bandwidth from tc output:%s" % str(se.groups()))
        logging.debug("bandwidth from setting:%s" % str(bandwidth))
        if "average" in bandwidth:
            if se.group(2) == 'M':
                tc_average = int(se.group(1)) * 1000
            else:
                tc_average = int(se.group(1))
            assert tc_average == int(bandwidth["average"]) * 8
        if "burst" in bandwidth:
            if se.group(4) == 'M':
                tc_burst = int(se.group(3)) * 1024
            else:
                tc_burst = int(se.group(3))
            assert tc_burst == int(bandwidth["burst"])

    def check_host_routes():
        """
        Check network routes on host
        """
        for rt in routes:
            try:
                route = ast.literal_eval(rt)
                addr = "%s/%s" % (route["address"], route["prefix"])
                cmd = "ip route list %s" % addr
                if "family" in route and route["family"] == "ipv6":
                    cmd = "ip -6 route list %s" % addr
                output = process.run(cmd, shell=True).stdout_text
                match_obj = re.search(r"via (\S+).*metric (\d+)", output)
                if match_obj:
                    via_addr = match_obj.group(1)
                    metric = match_obj.group(2)
                    logging.debug("via address %s for %s, matric is %s"
                                  % (via_addr, addr, metric))
                    assert via_addr == route["gateway"]
                    if "metric" in route:
                        assert metric == route["metric"]
            except KeyError:
                pass

    def run_bandwidth_test(check_net=False, check_iface=False):
        """
        Test bandwidth option for network or interface by tc command.
        """
        iface_inbound = ast.literal_eval(iface_bandwidth_inbound)
        iface_outbound = ast.literal_eval(iface_bandwidth_outbound)
        net_inbound = ast.literal_eval(net_bandwidth_inbound)
        net_outbound = ast.literal_eval(net_bandwidth_outbound)
        net_bridge_name = ast.literal_eval(net_bridge)["name"]
        iface_name = libvirt.get_ifname_host(vm_name, iface_mac)

        try:
            if check_net and net_inbound:
                # Check qdisc rules
                cmd = "tc -d qdisc show dev %s" % net_bridge_name
                qdisc_output = process.run(cmd, shell=True).stdout_text
                logging.debug("Bandwidth qdisc output: %s", qdisc_output)
                if not qdisc_output.count("qdisc ingress ffff:"):
                    test.fail("Can't find ingress setting")
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
                if "floor" in iface_inbound:
                    if not libvirt_version.version_compare(1, 0, 1):
                        test.cancel("Not supported Qos options 'floor'")

                    check_class_rules(net_bridge_name, "1:3",
                                      {'floor': iface_inbound["floor"]})

            # Check filter rules on interface outbound settings
            if check_iface and iface_outbound:
                check_filter_rules(iface_name, iface_outbound)
        except AssertionError:
            stacktrace.log_exc_info(sys.exc_info())
            test.fail("Failed to check network bandwidth")

    def check_name_ip(session):
        """
        Check dns resolving on guest
        """
        # Check if bind-utils is installed
        if "ubuntu" in vm.get_distro().lower():
            pkg = "bind9"
        else:
            pkg = "bind-utils"
        if not utils_package.package_install(pkg, session):
            test.error("Failed to install bind-utils on guest")
        # Run host command to check if hostname can be resolved
        if not guest_ipv4 and not guest_ipv6:
            test.fail("No ip address found from parameters")
        guest_ip = guest_ipv4 if guest_ipv4 else guest_ipv6
        cmd = "host %s | grep %s" % (guest_name, guest_ip)
        if session.cmd_status(cmd):
            test.fail("Can't resolve name %s on guest" % guest_name)

    def check_ipt_rules(check_ipv4=True, check_ipv6=False):
        """
        Check iptables for network/interface
        """
        br_name = ast.literal_eval(net_bridge)["name"]
        net_forward = ast.literal_eval(params.get("net_forward", "{}"))
        nat_attrs = ast.literal_eval(params.get("nat_attrs", "{}"))
        net_ipv4 = params.get("net_ipv4")
        net_ipv6 = params.get("net_ipv6")
        net_dev_in = ""
        net_dev_out = ""
        if "dev" in net_forward:
            net_dev_in = " -i %s" % net_forward["dev"]
            net_dev_out = " -o %s" % net_forward["dev"]
        if libvirt_version.version_compare(5, 1, 0):
            input_chain = "LIBVIRT_INP"
            output_chain = "LIBVIRT_OUT"
            postrouting_chain = "LIBVIRT_PRT"
            forward_filter = "LIBVIRT_FWX"
            forward_in = "LIBVIRT_FWI"
            forward_out = "LIBVIRT_FWO"
        else:
            input_chain = "INPUT"
            output_chain = "OUTPUT"
            postrouting_chain = "POSTROUTING"
            forward_filter = "FORWARD"
            forward_in = "FORWARD"
            forward_out = "FORWARD"
        ipt_rules = (
            "%s -i %s -p udp -m udp --dport 53 -j ACCEPT" % (input_chain, br_name),
            "%s -i %s -p tcp -m tcp --dport 53 -j ACCEPT" % (input_chain, br_name),
            "{0} -i {1} -o {1} -j ACCEPT".format(forward_filter, br_name),
            "%s -o %s -j REJECT --reject-with icmp" % (forward_in, br_name),
            "%s -i %s -j REJECT --reject-with icmp" % (forward_out, br_name))
        if check_ipv4:
            ipv4_rules = list(ipt_rules)
            ipv4_rules.extend(
                ["%s -i %s -p udp -m udp --dport 67 -j ACCEPT" % (input_chain, br_name),
                 "%s -i %s -p tcp -m tcp --dport 67 -j ACCEPT" % (input_chain, br_name),
                 "%s -o %s -p udp -m udp --dport 68 -j ACCEPT" % (output_chain, br_name),
                 "%s -o %s -p udp -m udp --dport 68 "
                 "-j CHECKSUM --checksum-fill" % (postrouting_chain, br_name)])
            ctr_rule = ""
            nat_rules = []
            if "mode" in net_forward and net_forward["mode"] == "nat":
                nat_port = ast.literal_eval(params.get("nat_port"))
                p_start = nat_port["start"]
                p_end = nat_port["end"]
                ctr_rule = " -m .* RELATED,ESTABLISHED"
                nat_rules = [("{0} -s {1} ! -d {1} -p tcp -j MASQUERADE"
                              " --to-ports {2}-{3}".format(postrouting_chain, net_ipv4, p_start, p_end)),
                             ("{0} -s {1} ! -d {1} -p udp -j MASQUERADE"
                              " --to-ports {2}-{3}".format(postrouting_chain, net_ipv4, p_start, p_end)),
                             ("{0} -s {1} ! -d {1}"
                              " -j MASQUERADE".format(postrouting_chain, net_ipv4))]
            if nat_rules:
                ipv4_rules.extend(nat_rules)
            if (net_ipv4 and "mode" in net_forward and
                    net_forward["mode"] in ["nat", "route"]):
                rules = [("%s -d %s%s -o %s%s -j ACCEPT"
                          % (forward_in, net_ipv4, net_dev_in, br_name, ctr_rule)),
                         ("%s -s %s -i %s%s -j ACCEPT"
                          % (forward_out, net_ipv4, br_name, net_dev_out))]
                ipv4_rules.extend(rules)

            output = process.run('iptables-save', shell=True).stdout_text
            logging.debug("iptables: %s", output)
            if "mode" in net_forward and net_forward["mode"] == "open":
                if re.search(r"%s|%s" % (net_ipv4, br_name), output, re.M):
                    test.fail("Find iptable rule for open mode")
                utils_libvirtd.libvirtd_restart()
                output_again = process.run('iptables-save', shell=True).stdout_text
                if re.search(r"%s|%s" % (net_ipv4, br_name), output_again, re.M):
                    test.fail("Find iptable rule for open mode after restart "
                              "libvirtd")
                else:
                    logging.info("Can't find iptable rule for open mode as expected")
            else:
                for ipt in ipv4_rules:
                    if not re.search(r"%s" % ipt, output, re.M):
                        test.fail("Can't find iptable rule:\n%s" % ipt)
            return ipv4_rules
        if check_ipv6:
            ipv6_rules = list(ipt_rules)
            ipt6_rules.extend([
                ("INPUT -i %s -p udp -m udp --dport 547 -j ACCEPT" % br_name)])
            if (net_ipv6 and "mode" in net_forward and
                    net_forward["mode"] in ["nat", "route"]):
                rules = [("%s -d %s%s -o %s -j ACCEPT"
                          % (forward_in, net_ipv6, net_dev_in, br_name)),
                         ("%s -s %s -i %s%s -j ACCEPT"
                          % (forward_out, net_ipv6, br_name, net_dev_out))]
                ipv6_rules.extend(rules)
            if "mode" in net_forward and net_forward["mode"] == "nat" \
                    and nat_attrs.get('ipv6') == 'yes':
                v6_nat_rules = [
                    "MASQUERADE\s+tcp\s+{0}\s+!{0}".format(net_ipv6),
                    "MASQUERADE\s+udp\s+{0}\s+!{0}".format(net_ipv6),
                    "MASQUERADE\s+all\s+{0}\s+!{0}".format(net_ipv6),
                ]
                v6_output = process.run('ip6tables -t nat -L', shell=True).stdout_text
                for rule in v6_nat_rules:
                    if not re.search(rule, v6_output):
                        test.fail('Rule %s missing from ip6tables output.' % rule)
                return ipv6_rules
            output = process.run("ip6tables-save", shell=True).stdout_text
            logging.debug("ip6tables: %s", output)
            if "mode" in net_forward and net_forward["mode"] == "open":
                if re.search(r"%s|%s" % (net_ipv6, br_name), output, re.M):
                    test.fail("Find ip6table rule for open mode")
                utils_libvirtd.libvirtd_restart()
                output_again = process.run('ip6tables-save', shell=True).stdout_text
                if re.search(r"%s|%s" % (net_ipv6, br_name), output_again, re.M):
                    test.fail("Find ip6table rule for open mode after restart "
                              "libvirtd")
            else:
                for ipt in ipv6_rules:
                    if not re.search(r"%s" % ipt, output, re.M):
                        test.fail("Can't find ip6table rule:\n%s" % ipt)
            return ipv6_rules

    def run_ip_test(session, ip_ver):
        """
        Check iptables on host and ipv6 address on guest
        """
        if ip_ver == "ipv6":
            # Clean up iptables rules for guest to get ipv6 address
            session.cmd_status("ip6tables -F")

        # It may take some time to get the ip address
        def get_ip_func():
            return utils_net.get_guest_ip_addr(session, iface_mac,
                                               ip_version=ip_ver)

        utils_misc.wait_for(get_ip_func, 5)
        if not get_ip_func():
            utils_net.restart_guest_network(session, iface_mac,
                                            ip_version=ip_ver)
            utils_misc.wait_for(get_ip_func, 5)
        vm_ip = get_ip_func()
        logging.debug("Guest has ip: %s", vm_ip)
        if not vm_ip:
            test.fail("Can't find ip address on guest")
        ip_gateway = net_ip_address
        if ip_ver == "ipv6":
            ip_gateway = net_ipv6_address
            # Cleanup ip6talbes on host for ping6 test
            process.system("ip6tables -F")
        if ip_gateway and not routes:
            ping_s, _ = ping(dest=ip_gateway, count=5,
                             timeout=10, session=session)
            if ping_s:
                test.fail("Failed to ping gateway address: %s" % ip_gateway)

    def run_guest_libvirt(session):
        """
        Check guest libvirt network
        """
        # Try to install required packages
        if "ubuntu" in vm.get_distro().lower():
            pkg = "libvirt-bin"
        else:
            pkg = "libvirt"
        if not utils_package.package_install(pkg, session):
            test.error("Failed to install libvirt package on guest")
        # Try to load tun module first
        session.cmd("lsmod | grep tun || modprobe  tun")
        # Check network state on guest
        cmd = ("service libvirtd restart; virsh net-info default"
               " | grep 'Active:.*yes'")
        if session.cmd_status(cmd):
            test.fail("'default' network isn't in active state")
        # Try to destroy&start default network on guest
        for opt in ['net-destroy', 'net-start']:
            cmd = "virsh %s default" % opt
            status, output = session.cmd_status_output(cmd)
            logging.debug("Run %s on guest exit %s, output %s"
                          % (cmd, status, output))
            if status:
                test.fail(output)
        if not utils_package.package_remove("libvirt*", session):
            test.error("Failed to remove libvirt packages on guest")

    def check_qdisc(expect_type):
        """
        check the qdisc type of the tap device

        :param expect_type: the expected qdisc type for the interface, for example, noqueue, mq, htb or others.
        """
        if not vm.is_alive():
            vm.start()
        mac = vm_xml.VMXML.get_iface_dev(vm_name)[0]
        iface_features = vm_xml.VMXML.get_iface_by_mac(vm_name, mac)
        target_dev = iface_features.get('target', {}).get('dev')
        if not target_dev:
            test.error("Failed to get the target dev name for qdisc test!")
        outputs = process.run("ip l show %s" % target_dev).stdout_text
        if not re.findall(expect_type, outputs):
            test.fail("The qdisc type should be %s, but it is not, check %s" % (expect_type, outputs))

    start_error = "yes" == params.get("start_error", "no")
    define_error = "yes" == params.get("define_error", "no")
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
    dhcp_start_ipv6 = params.get("dhcp_start_ipv6")
    dhcp_end_ipv6 = params.get("dhcp_end_ipv6")
    guest_name = params.get("guest_name")
    guest_ipv4 = params.get("guest_ipv4")
    guest_ipv6 = params.get("guest_ipv6")
    tftp_root = params.get("tftp_root")
    pxe_boot = "yes" == params.get("pxe_boot", "no")
    routes = params.get("routes", "").split()
    net_bandwidth_inbound = params.get("net_bandwidth_inbound", "{}")
    net_bandwidth_outbound = params.get("net_bandwidth_outbound", "{}")
    iface_bandwidth_inbound = params.get("iface_bandwidth_inbound", "{}")
    iface_bandwidth_outbound = params.get("iface_bandwidth_outbound", "{}")
    iface_num = params.get("iface_num", "1")
    iface_source = params.get("iface_source", "{}")
    iface_rom = params.get("iface_rom")
    iface_boot = params.get("iface_boot")
    iface_model = params.get("iface_model")
    iface_driver = params.get("iface_driver")
    multiple_guests = params.get("multiple_guests")
    create_network = "yes" == params.get("create_network", "no")
    attach_iface = "yes" == params.get("attach_iface", "no")
    serial_login = "yes" == params.get("serial_login", "no")
    change_iface_option = "yes" == params.get("change_iface_option", "no")
    test_bridge = "yes" == params.get("test_bridge", "no")
    test_dnsmasq = "yes" == params.get("test_dnsmasq", "no")
    test_dhcp_range = "yes" == params.get("test_dhcp_range", "no")
    test_dns_host = "yes" == params.get("test_dns_host", "no")
    test_qos_bandwidth = "yes" == params.get("test_qos_bandwidth", "no")
    test_pg_bandwidth = "yes" == params.get("test_portgroup_bandwidth", "no")
    test_qos_remove = "yes" == params.get("test_qos_remove", "no")
    test_ipv4_address = "yes" == params.get("test_ipv4_address", "no")
    test_ipv6_address = "yes" == params.get("test_ipv6_address", "no")
    test_guest_libvirt = "yes" == params.get("test_guest_libvirt", "no")
    test_dns_forwarders = "yes" == params.get("test_dns_forwarders", "no")
    test_vm_clone = "yes" == params.get("test_vm_clone", "no")
    net_no_bridge = "yes" == params.get("no_bridge", "no")
    net_no_mac = "yes" == params.get("no_mac", "no")
    net_no_ip = "yes" == params.get("no_ip", "no")
    net_with_dev = "yes" == params.get("with_dev", "no")
    update_device = 'yes' == params.get('update_device', 'no')
    remove_bandwidth = 'yes' == params.get('remove_bandwidth', 'no')
    with_2net = 'yes' == params.get('with_2net', 'no')
    loop = int(params.get('loop', 0))
    username = params.get("username")
    password = params.get("password")
    forward = ast.literal_eval(params.get("net_forward", "{}"))
    boot_failure = "yes" == params.get("boot_failure", "no")
    test_netmask = "yes" == params.get("test_netmask", "no")
    ipt_rules = []
    ipt6_rules = []
    define_macvtap = "yes" == params.get("define_macvtap", "no")
    net_dns_forwarders = params.get("net_dns_forwarders", "").split()

    # Destroy VM first
    if vm.is_alive() and not update_device:
        vm.destroy(gracefully=False)

    # Back up xml file.
    netxml_backup = NetworkXML.new_from_net_dumpxml("default")
    iface_mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)
    params["guest_mac"] = iface_mac
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vms_list = []
    if "floor" in ast.literal_eval(iface_bandwidth_inbound):
        if not libvirt_version.version_compare(1, 0, 1):
            test.cancel("Not supported Qos options 'floor'")

    if not libvirt_version.version_compare(6, 5, 0):
        if 'nat_ipv6' in params['name']:
            test.cancel('NAT ipv6 is not supported until 6.5.0')
        if 'with_2net' in params['name']:
            test.cancel('Test case might fail before 6.5.0 where it was'
                        ' fixed with libvirt commit'
                        ' 876211ef4a192df1603b45715044ec14567d7e9f')

    # Enabling IPv6 forwarding with RA routes without accept_ra set to 2
    # is likely to cause routes loss
    sysctl_cmd = 'sysctl net.ipv6.conf.all.accept_ra'
    original_accept_ra = process.run(sysctl_cmd + ' -n', shell=True).stdout_text
    if test_ipv6_address and original_accept_ra != '2':
        process.system(sysctl_cmd + '=2')

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
            net_ifs = utils_net.get_net_if(state="UP")
            # Check forward device is valid or not,
            # if it's not in host interface list, try to set
            # forward device to first active interface of host
            if ('mode' in forward and forward['mode'] in
                    ['passthrough', 'private', 'bridge', 'macvtap'] and
                    'dev' in forward and
                    forward['dev'] not in net_ifs):
                logging.warning("Forward device %s is not a interface of host, reset to %s",
                                forward['dev'], net_ifs[0])
                forward['dev'] = net_ifs[0]
                params["net_forward"] = str(forward)
            if define_macvtap:
                for i in [0, 2, 4]:
                    cmd = "ip l add li %s macvtap%s type macvtap" % (forward['dev'], i)
                    process.run(cmd, shell=True, verbose=True)
                process.run("ip l", shell=True, verbose=True)
            forward_iface = params.get("forward_iface")
            if forward_iface:
                interface = [x for x in forward_iface.split()]
                # The guest will use first interface of the list,
                # check if it's valid or not, if it's not in host
                # interface list, try to set forward interface to
                # first active interface of host.
                if interface[0] not in net_ifs:
                    logging.warning("Forward interface %s is not a interface of host, reset to %s",
                                    interface[0], net_ifs[0])
                    interface[0] = net_ifs[0]
                    params["forward_iface"] = " ".join(interface)

            netxml = libvirt.create_net_xml(net_name, params)
            if "mode" in forward and forward["mode"] == "open":
                netxml.mac = utils_net.generate_mac_address_simple()
                try:
                    if net_no_bridge:
                        netxml.del_bridge()
                    if net_no_ip:
                        netxml.del_ip()
                        netxml.del_ip()
                    if net_no_mac:
                        netxml.del_mac()
                except xcepts.LibvirtXMLNotFoundError:
                    pass
                if net_with_dev:
                    net_forward = netxml.forward
                    net_forward.update({"dev": net_ifs[0]})
                    netxml.forward = net_forward
            logging.info("netxml before define is %s", netxml)
            if with_2net:
                net2_net_name = params.get('net2_net_name', 'net2')
                net2_params = {k.replace('net2_', ''): v for k, v in params.items()
                               if k.startswith('net2_')}
                logging.debug('net2 params are: %s', net2_params)
                net2_xml = libvirt.create_net_xml(net2_net_name, net2_params)
                logging.debug('Second network: %s', net2_xml)
                virsh.net_define(net2_xml.xml, debug=True, ignore_status=False)
                virsh.net_start(net2_net_name, ignore_status=False)
            try:
                netxml.sync()
            except xcepts.LibvirtXMLError as details:
                logging.info(str(details))
                if define_error:
                    return
                else:
                    test.fail("Failed to define network")

        # Check open mode network xml
        if "mode" in forward and forward["mode"] == "open":
            netxml_new = NetworkXML.new_from_net_dumpxml(net_name)
            logging.info("netxml after define is %s", netxml_new)
            try:
                if net_no_bridge:
                    net_bridge = str(netxml_new.bridge)
                if net_no_mac:
                    netxml_new.mac
            except xcepts.LibvirtXMLNotFoundError as details:
                test.fail("Failed to check %s xml: %s" % (net_name, details))
            logging.info("mac/bridge still exist even if removed before define")

        # Edit the interface xml.
        if change_iface_option:
            try:
                if update_device:
                    updated_iface = modify_iface_xml(sync=False)
                    if loop == 3:
                        check_qdisc("noqueue")
                    else:
                        check_qdisc("htb")
                    virsh.update_device(vm_name, updated_iface.xml,
                                        ignore_status=False, debug=True)
                    if loop in (2, 3):
                        check_qdisc("htb")
                    else:
                        check_qdisc("noqueue")
                else:
                    modify_iface_xml()
                    if with_2net:
                        # Create another interface attaching with 2rd network
                        new_iface = Interface('network')
                        new_iface.source = eval(net2_params['iface_source'])
                        new_iface.model = net2_params['iface_model']
                        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                        vmxml.add_device(new_iface)
                        vmxml.sync()
                        logging.debug(virsh.dumpxml(vm_name).stdout_text)
            except xcepts.LibvirtXMLError as details:
                logging.info(str(details))
                if define_error:
                    if not str(details).count("Failed to define"):
                        test.fail("VM sync failed msg not expected")
                    return
                else:
                    test.fail("Failed to sync VM")
        # Attach interface if needed
        if attach_iface:
            iface_type = params.get("iface_type", "network")
            for i in range(int(iface_num)):
                logging.info("Try to attach interface loop %s" % i)
                options = ("%s %s --model %s --config" %
                           (iface_type, net_name, iface_model))
                ret = virsh.attach_interface(vm_name, options,
                                             ignore_status=True)
                if ret.exit_status:
                    logging.error("Command output %s" %
                                  ret.stdout.strip())
                    test.fail("Failed to attach-interface")

        # Test Starting vm with 2 interfaces connected to 2 different
        # nat network and check net-dhcp-leases
        if with_2net:
            net1_br = NetworkXML.new_from_net_dumpxml(net_name).bridge['name']
            net2_br = NetworkXML.new_from_net_dumpxml(net2_net_name).bridge['name']
            status_path = '/var/lib/libvirt/dnsmasq/%s.status'
            args = {'ignore_status': False, 'debug': True}
            # Test several rounds to make sure no error
            test_rounds = int(params.get('test_rounds', 20))
            for i in range(test_rounds):
                logging.debug('Test round %d', i + 1)
                os.remove(status_path % net1_br)
                os.remove(status_path % net2_br)
                virsh.net_destroy(net_name, **args)
                virsh.net_destroy(net2_net_name, **args)
                virsh.net_start(net_name, **args)
                virsh.net_start(net2_net_name, **args)
                virsh.start(vm_name, **args)

                def _check_lease(loop):
                    out1 = virsh.net_dhcp_leases(net_name, **args).stdout_text
                    out2 = virsh.net_dhcp_leases(net2_net_name, **args).stdout_text
                    if all(['ipv' in x for x in [out1, out2]]):
                        logging.debug('Found DHCP lease of round %d.', loop + 1)
                        return True
                if not utils_misc.wait_for(lambda: _check_lease(i), 60):
                    test.fail('DHCP lease not found of round %d' % (i + 1))
                vm.destroy()
            return

        if multiple_guests:
            # Clone more vms for testing
            for i in range(int(multiple_guests)):
                guest_name = "%s_%s" % (vm_name, i)
                timeout = params.get("clone_timeout", 360)
                utils_libguestfs.virt_clone_cmd(vm_name, guest_name,
                                                True, timeout=timeout)
                vms_list.append(vm.clone(guest_name))

        if test_bridge:
            bridge = ast.literal_eval(net_bridge)
            br_if = utils_net.Interface(bridge['name'])
            if not br_if.is_up():
                test.fail("Bridge interface isn't up")
        if test_dnsmasq:
            # Check dnsmasq process
            dnsmasq_cmd = process.run("ps -aux|grep dnsmasq", shell=True).stdout_text
            logging.debug(dnsmasq_cmd)
            if not re.search("dnsmasq --conf-file=/var/lib/libvirt/dnsmasq/%s.conf"
                             % net_name, dnsmasq_cmd):
                test.fail("Can not find dnsmasq process or the process is not correct")

            # Check the settings in dnsmasq config file
            if net_dns_forward == "no":
                run_dnsmasq_default_test("domain-needed")
                run_dnsmasq_default_test("local", "//")
            if net_domain:
                run_dnsmasq_default_test("domain", net_domain)
                run_dnsmasq_default_test("expand-hosts")
            if net_bridge:
                bridge = ast.literal_eval(net_bridge)
                run_dnsmasq_default_test("interface", bridge['name'], name=net_name)
                if 'stp' in bridge and bridge['stp'] == 'on':
                    if 'delay' in bridge and bridge['delay'] != '0':
                        # network xml forward delay value in seconds, while on
                        # host, check by ip command, the value is in second*100
                        br_delay = int(bridge['delay']) * 100
                        logging.debug("Expect forward_delay is %s ms" % br_delay)
                        cmd = ("ip -d link sh %s | grep 'bridge forward_delay'"
                               % bridge['name'])
                        out = process.run(
                            cmd, shell=True, ignore_status=False).stdout_text
                        logging.debug("bridge statistics output: %s", out)
                        pattern = (r"\s*bridge forward_delay\s+(\d+)")
                        match_obj = re.search(pattern, out, re.M)
                        if not match_obj:
                            test.fail("Can't see forward delay messages from command")
                        elif int(match_obj.group(1)) != br_delay:
                            test.fail("Foward delay setting can't take effect")
                        else:
                            logging.debug("Foward delay set successfully!")
            if dhcp_start_ipv4 and dhcp_end_ipv4:
                run_dnsmasq_default_test("dhcp-range", "%s,%s"
                                         % (dhcp_start_ipv4, dhcp_end_ipv4),
                                         name=net_name)
            if dhcp_start_ipv6 and dhcp_end_ipv6:
                run_dnsmasq_default_test("dhcp-range", "%s,%s,64"
                                         % (dhcp_start_ipv6, dhcp_end_ipv6),
                                         name=net_name)
            if guest_name and guest_ipv4:
                run_dnsmasq_host_test(iface_mac, guest_ipv4, guest_name)

            if test_netmask and libvirt_version.version_compare(5, 1, 0):
                run_dnsmasq_default_test("dhcp-range", "192.168.122.2,192.168.122.254,255.255.252.0")
            # check the left part in dnsmasq conf
            run_dnsmasq_default_test("strict-order", name=net_name)
            if libvirt_version.version_compare(6, 0, 0):
                run_dnsmasq_default_test("pid-file", "/run/libvirt/network/%s.pid" % net_name, name=net_name)
            else:
                run_dnsmasq_default_test("pid-file", "/var/run/libvirt/network/%s.pid" % net_name, name=net_name)
            run_dnsmasq_default_test("except-interface", "lo", name=net_name)
            run_dnsmasq_default_test("bind-dynamic", name=net_name)
            run_dnsmasq_default_test("dhcp-no-override", name=net_name)
            if dhcp_start_ipv6 and dhcp_start_ipv4:
                run_dnsmasq_default_test("dhcp-lease-max", "493", name=net_name)
            else:
                range_num = int(params.get("dhcp_range", "252"))
                run_dnsmasq_default_test("dhcp-lease-max", str(range_num + 1), name=net_name)
            run_dnsmasq_default_test("dhcp-hostsfile",
                                     "/var/lib/libvirt/dnsmasq/%s.hostsfile" % net_name,
                                     name=net_name)
            run_dnsmasq_default_test("addn-hosts",
                                     "/var/lib/libvirt/dnsmasq/%s.addnhosts" % net_name,
                                     name=net_name)
            if dhcp_start_ipv6:
                run_dnsmasq_default_test("enable-ra", name=net_name)

        if test_dns_host:
            if net_dns_txt:
                dns_txt = ast.literal_eval(net_dns_txt)
                run_dnsmasq_default_test("txt-record", "%s,%s" %
                                         (dns_txt["name"],
                                          dns_txt["value"]))
            if net_dns_srv:
                dns_srv = ast.literal_eval(net_dns_srv)
                run_dnsmasq_default_test("srv-host", "_%s._%s.%s,%s,%s,%s,%s" %
                                         (dns_srv["service"], dns_srv["protocol"],
                                          dns_srv["domain"], dns_srv["target"],
                                          dns_srv["port"], dns_srv["priority"],
                                          dns_srv["weight"]))
            if net_dns_hostip and net_dns_hostnames:
                run_dnsmasq_addnhosts_test(net_dns_hostip, net_dns_hostnames)
        if test_dns_forwarders:
            if net_name == "isolatedtest":
                run_dnsmasq_default_test("no-resolv", name=net_name)
            else:
                net_dns_forwarder = [ast.literal_eval(x) for x in net_dns_forwarders]
                for forwarder in net_dns_forwarder:
                    if ('domain' in forwarder) and ('addr' in forwarder):
                        run_dnsmasq_default_test("server", "/%s/%s" % (forwarder['domain'], forwarder['addr']))
                    elif "domain" in forwarder:
                        run_dnsmasq_default_test("server", "/%s/#" % forwarder['domain'])
                    elif "addr" in forwarder:
                        run_dnsmasq_default_test("server", "%s" % forwarder['addr'])
                        run_dnsmasq_default_test("no-resolv")
        # Run bandwidth test for network
        if test_qos_bandwidth and not update_device:
            run_bandwidth_test(check_net=True)

        # If to remove bandwidth from iface,
        # update iface xml to the original one
        if remove_bandwidth:
            check_qdisc("htb")
            inbound = outbound = '{"average":0}'
            iface_dict = {"inbound": inbound, "outbound": outbound}
            new_iface_xml = libvirt.modify_vm_iface(vm_name, "get_xml", iface_dict)
            virsh.update_device(vm_name, new_iface_xml, ignore_status=False, debug=True)
            check_qdisc("noqueue")
        # Check routes if needed
        if routes:
            check_host_routes()

        try:
            # Start the VM.
            if not update_device and not test_vm_clone and not remove_bandwidth:
                vm.start()
            if start_error:
                test.fail("VM started unexpectedly")
            if define_macvtap:
                cmd = "ls /sys/devices/virtual/net"
                output = process.run(cmd, shell=True, verbose=True).stdout_text
                macvtap_list = re.findall(r'macvtap0|macvtap1|macvtap2|macvtap3'
                                          r'|macvtap4|macvtap5|macvtap6|macvtap7',
                                          output)
                logging.debug("The macvtap_list is %s" % macvtap_list)
                if set(macvtap_list) != set(['macvtap' + str(x) for x in range(8)]):
                    test.fail("Existing macvtap device %s is not expected! should be macvtap(0-7)" % macvtap_list)
            if pxe_boot:
                # Just check network boot messages here
                try:
                    vm.serial_console.read_until_output_matches(
                        ["Loading vmlinuz", "Loading initrd.img"],
                        utils_misc.strip_console_codes)
                except ExpectTimeoutError as details:
                    if boot_failure:
                        logging.info("Fail to boot from pxe as expected")
                    else:
                        test.fail("Fail to boot from pxe")
            elif test_vm_clone:
                logging.debug(virsh.dumpxml(vm_name).stdout_text)
                vm_clone = vm_name + utils_misc.generate_random_string(3)
                utils_libguestfs.virt_clone_cmd(vm_name, vm_clone, True,
                                                debug=True, timeout=300)
                vms_list.append(vm.clone(vm_clone))
                start_clone_vm = virsh.start(vm_clone, debug=True)
                libvirt.check_exit_status(start_clone_vm)
            else:
                if serial_login:
                    session = vm.wait_for_serial_login(username=username,
                                                       password=password)
                else:
                    session = vm.wait_for_login()

                if test_dhcp_range:
                    dhcp_range = int(params.get("dhcp_range", "252"))
                    utils_net.restart_guest_network(session, iface_mac)
                    vm_ip = utils_net.get_guest_ip_addr(session, iface_mac)
                    logging.debug("Guest has ip: %s", vm_ip)
                    if not vm_ip and dhcp_range:
                        test.fail("Guest has invalid ip address")
                    elif vm_ip and not dhcp_range:
                        test.fail("Guest has ip address: %s" % vm_ip)
                    dhcp_range = dhcp_range - 1
                    for vms in vms_list:
                        # Start other VMs.
                        vms.start()
                        sess = vms.wait_for_serial_login()
                        vms_mac = vms.get_virsh_mac_address()
                        # restart guest network to get ip addr
                        utils_net.restart_guest_network(sess, vms_mac)
                        vms_ip = utils_net.get_guest_ip_addr(sess,
                                                             vms_mac)
                        if not vms_ip and dhcp_range:
                            test.fail("Guest has invalid ip address")
                        elif vms_ip and not dhcp_range:
                            # Get IP address on guest should return Null
                            # if it exceeds the dhcp range
                            test.fail("Guest has ip address: %s" % vms_ip)
                        dhcp_range = dhcp_range - 1
                        if vms_ip:
                            ping_s, _ = ping(dest=vm_ip, count=5,
                                             timeout=10, session=sess)
                            if ping_s:
                                test.fail("Failed to ping, src: %s, "
                                          "dst: %s" % (vms_ip, vm_ip))
                        sess.close()

                # Check dnsmasq settings if take affect in guest
                if guest_ipv4:
                    check_name_ip(session)
                # Run bandwidth test for interface
                if test_qos_bandwidth:
                    run_bandwidth_test(check_iface=True)
                # Run bandwidth test for portgroup
                if test_pg_bandwidth:
                    pg_bandwidth_inbound = params.get(
                        "portgroup_bandwidth_inbound", "").split()
                    pg_bandwidth_outbound = params.get(
                        "portgroup_bandwidth_outbound", "").split()
                    pg_name = params.get("portgroup_name", "").split()
                    pg_default = params.get("portgroup_default", "").split()
                    iface_inbound = ast.literal_eval(iface_bandwidth_inbound)
                    iface_outbound = ast.literal_eval(iface_bandwidth_outbound)
                    iface_name = libvirt.get_ifname_host(vm_name, iface_mac)
                    if_source = ast.literal_eval(iface_source)
                    if "portgroup" in if_source:
                        pg = if_source["portgroup"]
                    else:
                        pg = "default"
                    for (name, df, bw_ib, bw_ob) in zip(pg_name, pg_default,
                                                        pg_bandwidth_inbound,
                                                        pg_bandwidth_outbound):
                        if pg == name:
                            inbound = ast.literal_eval(bw_ib)
                            outbound = ast.literal_eval(bw_ob)
                        elif pg == "default" and df == "yes":
                            inbound = ast.literal_eval(bw_ib)
                            outbound = ast.literal_eval(bw_ob)
                        else:
                            continue
                        # Interface bandwidth settings will
                        # overwriting portgroup settings
                        if iface_inbound:
                            inbound = iface_inbound
                        if iface_outbound:
                            outbound = iface_outbound
                        check_class_rules(iface_name, "1:1", inbound)
                        check_filter_rules(iface_name, outbound)
                if test_qos_remove:
                    # Remove the bandwidth settings in network xml
                    logging.debug("Removing network bandwidth settings...")
                    netxml_backup.sync()
                    vm.destroy(gracefully=False)
                    # Should fail to start vm
                    vm.start()
                    if restart_error:
                        test.fail("VM started unexpectedly")
                if test_ipv6_address:
                    ipt6_rules = check_ipt_rules(check_ipv4=False, check_ipv6=True)
                    if not ("mode" in forward and forward["mode"] == "open"):
                        run_ip_test(session, "ipv6")
                if test_ipv4_address:
                    ipt_rules = check_ipt_rules(check_ipv4=True)
                    if not ("mode" in forward and forward["mode"] == "open"):
                        run_ip_test(session, "ipv4")
                if test_guest_libvirt:
                    run_guest_libvirt(session)

                session.close()
        except virt_vm.VMStartError as details:
            logging.info(str(details))
            if not (start_error or restart_error):
                test.fail('VM failed to start:\n%s' % details)

        # Destroy created network and check iptable rules
        if net_name != "default":
            virsh.net_destroy(net_name)
        if ipt_rules:
            output_des = process.run('iptables-save', shell=True).stdout_text
            for ipt in ipt_rules:
                if re.search(r"%s" % ipt, output_des, re.M):
                    test.fail("Find iptable rule %s after net destroyed" % ipt)
        if ipt6_rules:
            output_des = process.run('ip6tables-save', shell=True).stdout_text
            for ipt in ipt6_rules:
                if re.search(r"%s" % ipt, output_des, re.M):
                    test.fail("Find ip6table rule %s after net destroyed" % ipt)
        if remove_bandwidth:
            iface_name = libvirt.get_ifname_host(vm_name, iface_mac)
            cur_xml = virsh.dumpxml(vm_name).stdout_text
            logging.debug(cur_xml)
            if 'bandwidth' in cur_xml:
                test.fail('bandwidth still in xml')
            if not check_filter_rules(iface_name, 0, expect_none=True):
                test.fail('There should be nothing in output')
        if update_device and loop:
            loop -= 1
            if loop:
                logging.debug("Current loop is %s" % loop)
                # Rerun this procedure again with updated params
                # Reset params of the corresponding loop
                loop_prefix = 'loop' + str(loop) + '_'
                for k in {k: v for k, v in params.items() if k.startswith(loop_prefix)}:
                    params[k.lstrip(loop_prefix)] = params[k]
                params['loop'] = str(loop)
                run(test, params, env)

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
        if 'net2_net_name' in locals():
            virsh.net_destroy(net2_net_name)
            virsh.net_undefine(net2_net_name)
        vmxml_backup.sync()

        if test_ipv6_address and original_accept_ra != '2':
            process.system(sysctl_cmd + "=%s" % original_accept_ra)
        if define_macvtap:
            cmd = "ip l del macvtap0; ip l del macvtap2; ip l del macvtap4"
            process.run(cmd, shell=True, verbose=True)
