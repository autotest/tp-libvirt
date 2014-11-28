import os
import re
import logging
from autotest.client.shared import error
from virttest import virt_vm, virsh
from virttest import utils_net
from virttest import utils_libguestfs
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.libvirt_xml.network_xml import DNSXML


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
    virsh_dargs = {'debug': True, 'ignore_status': True}

    def modify_net_xml():
        """
        Modify network xml options
        """
        netxml = NetworkXML.new_from_net_dumpxml(net_name)
        dns_dict = {}
        host_dict = {}
        if net_dns_forward:
            dns_dict["dns_forward"] = net_dns_forward
        if net_dns_txt:
            dns_dict["txt"] = eval(net_dns_txt)
        if net_dns_srv:
            dns_dict["srv"] = eval(net_dns_srv)
        if net_dns_forwarders:
            dns_dict["forwarders"] = [eval(x) for x in net_dns_forwarders]
        if net_dns_hostip:
            host_dict["host_ip"] = net_dns_hostip
        if net_dns_hostnames:
            host_dict["hostnames"] = net_dns_hostnames

        dns_obj = netxml.new_dns(**dns_dict)
        if host_dict:
            host = dns_obj.new_host(**host_dict)
            dns_obj.host = host
        netxml.dns = dns_obj
        logging.debug("New dns xml: %s", dns_obj)

        if net_domain:
            netxml.domain_name = net_domain
        ipxml = netxml.get_ip()
        if guest_name and guest_ip:
            ipxml.hosts = [{"mac": iface_mac,
                            "name": guest_name,
                            "ip": guest_ip}]
        logging.debug("ipxml: %s" % ipxml)
        if dhcp_start and dhcp_end:
            ipxml.address = dhcp_start
            ipxml.dhcp_ranges = {"start": dhcp_start, "end": dhcp_end}
        netxml.set_ip(ipxml)

        logging.debug("New network xml file: %s", netxml)
        netxml.xmltreefile.write()
        netxml.sync()

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

    def check_name_ip(session):
        """
        Check dns resolving on guest
        """
        # Check if bind-utils is installed
        cmd = "rpm -q bind-utils || yum install -y bind-utils"
        stat_install, output = session.cmd_status_output(cmd, 300)
        logging.debug(output)
        if stat_install:
            raise error.TestFail("Failed to install bind-utils"
                                 " on guest")
        # Run host command to check if hostname can be resolved
        cmd = "host %s | grep %s" % (guest_name, guest_ip)
        if session.cmd_status(cmd):
            raise error.TestFail("Can't resolve name %s on guest" %
                                 guest_name)

    status_error = "yes" == params.get("status_error", "no")
    start_error = "yes" == params.get("start_error", "no")

    # network specific attributes.
    net_name = params.get("net_name", "default")
    net_domain = params.get("net_domain")
    net_dns_forward = params.get("net_dns_forward")
    net_dns_forwarders = params.get("net_dns_forwarders", "").split()
    net_dns_txt = params.get("net_dns_txt")
    net_dns_srv = params.get("net_dns_srv")
    net_dns_hostip = params.get("net_dns_hostip")
    net_dns_hostnames = params.get("net_dns_hostnames", "").split()
    guest_name = params.get("guest_name")
    guest_ip = params.get("guest_ip")
    dhcp_start = params.get("dhcp_start")
    dhcp_end = params.get("dhcp_end")
    multiple_guests = params.get("multiple_guests")
    change_option = "yes" == params.get("change_net_option", "no")
    test_dnsmasq = "yes" == params.get("test_dnsmasq", "no")
    test_dhcp_range = "yes" == params.get("test_dhcp_range", "no")
    test_dns_host = "yes" == params.get("test_dns_host", "no")

    # Set serial console for serial login
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    # Setting console to kernel parameters
    vm.set_kernel_console("ttyS0", "115200")
    vm.shutdown()

    # Back up xml file.
    netxml_backup = NetworkXML.new_from_net_dumpxml(net_name)
    iface_mac = vm_xml.VMXML.get_first_mac_by_name(vm_name)
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

        # Edit the interface xml.
        if change_option:
            modify_net_xml()

        if multiple_guests:
            # Clone more vms for testing
            for i in range(int(multiple_guests)):
                guest_name = "%s_%s" % (vm_name, i)
                utils_libguestfs.virt_clone_cmd(vm_name, guest_name, True)
                vms_list.append(vm.clone(guest_name))

        if test_dnsmasq:
            # Check the settings in dnsmasq config file
            if net_dns_forward == "no":
                run_dnsmasq_default_test("domain-needed")
                run_dnsmasq_default_test("local", "//")
            if net_domain:
                run_dnsmasq_default_test("domain", net_domain)
                run_dnsmasq_default_test("expand-hosts")
            if guest_name and guest_ip:
                run_dnsmasq_host_test(iface_mac, guest_ip, guest_name)

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

        # Start the VM.
        vm.start()
        session = vm.wait_for_login()
        if start_error:
            raise error.TestFail("VM started unexpectedly")

        # Start other VMs.
        if test_dhcp_range:
            for vms in vms_list:
                vms.start()
                session = vms.wait_for_serial_login()
                vms_mac = vms.get_virsh_mac_address()
                # restart guest network to get ip addr
                utils_net.restart_guest_network(session, vms_mac)
                vms_ip = utils_net.get_guest_ip_addr(session,
                                                     vms_mac)
                if vms_ip:
                    # Get IP address on guest should return Null
                    raise error.TestFail("Guest has ip address: %s"
                                         % vms_ip)
                session.close()

        # Check dnsmasq settings if take affect in guest
        if test_dnsmasq:
            if guest_name and guest_ip:
                check_name_ip(session)

        session.close()
    except virt_vm.VMStartError, e:
        logging.error(str(e))
        if start_error:
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
        netxml_backup.sync()
