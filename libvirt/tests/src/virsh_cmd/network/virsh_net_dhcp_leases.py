import re
import logging

from autotest.client.shared import error

from virttest import utils_net
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import network_xml
from virttest.utils_test import libvirt as utlv

BUG_URL = "https://bugzilla.redhat.com/show_bug.cgi?id=%s"


def run(test, params, env):
    """
    Test command: virsh net-dhcp-leases

    1. Create a new network and run virsh command to check dhcp leases info.
    2. Attach an interface before or after start the domain, then check the
       dhcp leases info.
    3. Clean the environment.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    net_name = params.get("net_name", "default")
    nic_mac = params.get("nic_mac", "")
    net_option = params.get("net_option", "")
    status_error = "yes" == params.get("status_error", "no")
    prepare_net = "yes" == params.get("prepare_net", "yes")
    hotplug_iface = "yes" == params.get("hotplug_interface", "no")
    filter_by_mac = "yes" == params.get("filter_by_mac", "no")
    exist_bug = params.get("exist_bug")

    def create_network():
        """
        Create a network
        """
        net_ip_addr = params.get("net_ip_addr", "192.168.200.1")
        net_ip_netmask = params.get("net_ip_netmask", "255.255.255.0")
        net_dhcp_start = params.get("net_dhcp_start", "192.168.200.2")
        net_dhcp_end = params.get("net_dhcp_end", "192.168.200.254")
        netxml = network_xml.NetworkXML()
        netxml.name = net_name
        netxml.forward = {'mode': "nat"}
        ipxml = network_xml.IPXML()
        ipxml.address = net_ip_addr
        ipxml.netmask = net_ip_netmask
        ipxml.dhcp_ranges = {'start': net_dhcp_start, "end": net_dhcp_end}
        netxml.set_ip(ipxml)
        netxml.create()

    def get_net_dhcp_leases(output):
        """
        Return the dhcp lease info in a list
        """
        leases = []
        lines = output.splitlines()
        if not lines:
            return leases
        try:
            pat = r"\S+\ ?\S+\ ?\S+\ ?\S+|\S+"
            keys = re.findall(pat, lines[0])
            for line in lines[2:]:
                values = re.findall(pat, line)
                leases.append(dict(zip(keys, values)))
            return leases
        except:
            raise error.TestError("Fail to parse output: %s" % output)

    def get_ip_by_mac(mac_addr, try_dhclint=False):
        """
        Get interface IP address by given MAC addrss. If try_dhclint is
        True, then try to allocate IP addrss for the interface.
        """
        session = vm.wait_for_login(new_nic_index)

        def f():
            return utils_net.get_guest_ip_addr(session, mac_addr)

        try:
            ip_addr = utils_misc.wait_for(f, 10)
            if ip_addr is None:
                iface_name = utils_net.get_linux_ifname(session, mac_addr)
                if try_dhclint:
                    session.cmd("dhclient %s" % iface_name)
                    ip_addr = utils_misc.wait_for(f, 10)
                else:
                    # No IP for the interface, just print the interface name
                    logging.warn("Find '%s' with MAC address '%s', "
                                 "but which has no IP address", iface_name,
                                 mac_addr)
        finally:
            session.close()
        return ip_addr

    def check_net_lease(net_leases, expected_find=True):
        """
        Check the dhcp lease info.
        """
        if not net_leases:
            if expected_find:
                raise error.TestFail("Lease info is empty")
            else:
                logging.debug("No dhcp lease info find as expected")
        else:
            if not expected_find:
                raise error.TestFail("Find unexpected dhcp lease info: %s"
                                     % net_leases)
        find_mac = False
        for net_lease in net_leases:
            net_mac = net_lease['MAC address']
            net_ip = net_lease['IP address'][:-3]
            if vm_xml.VMXML.get_iface_by_mac(vm_name, net_mac):
                find_mac = True
                logging.debug("Find '%s' in domain XML", net_mac)
            else:
                logging.debug("Not find '%s' in domain XML", net_mac)
                continue
            iface_ip = get_ip_by_mac(net_mac)
            if iface_ip and iface_ip != net_ip:
                raise error.TestFail("Address '%s' is not expected" % iface_ip)
        if expected_find and not find_mac:
            raise error.TestFail("No matched MAC address")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    if vm.is_alive():
        vm.destroy(gracefully=False)
    # Get the nic index for new attached interface
    new_nic_index = len(vmxml.get_iface_all())
    # Create new network
    if prepare_net:
        create_network()
    nets = virsh.net_state_dict()
    if net_name not in nets.keys() and not status_error:
        raise error.TestError("Not find network '%s'" % net_name)
    expected_find = False
    try:
        result = virsh.net_dhcp_leases(net_name,
                                       mac=nic_mac,
                                       options=net_option,
                                       debug=True,
                                       ignore_status=True)
        utlv.check_exit_status(result, status_error)
        lease = get_net_dhcp_leases(result.stdout.strip())
        check_net_lease(lease, expected_find)
        if not status_error:
            iface_mac = utils_net.generate_mac_address_simple()
            if filter_by_mac:
                nic_mac = iface_mac
            op = "--type network --source %s --mac %s" % (net_name, iface_mac)
            nic_params = {'mac': iface_mac, 'nettype': 'bridge',
                          'ip_version': 'ipv4'}
            if not hotplug_iface:
                op += " --config"
                virsh.attach_interface(vm_name, option=op, debug=True,
                                       ignore_status=False)
                vm.add_nic(**nic_params)
                vm.start()
            else:
                vm.start()
                virsh.attach_interface(vm_name, option=op, debug=True,
                                       ignore_status=False)
                vm.add_nic(**nic_params)
            new_interface_ip = get_ip_by_mac(iface_mac, try_dhclint=True)
            # Allocate IP address for the new interface may fail, so only
            # check the result if get new IP address
            if new_interface_ip:
                expected_find = True
            result = virsh.net_dhcp_leases(net_name, mac=nic_mac,
                                           debug=False, ignore_status=True)
            utlv.check_exit_status(result, status_error)
            lease = get_net_dhcp_leases(result.stdout.strip())
            check_net_lease(lease, expected_find)
    finally:
        if exist_bug:
            logging.warn("Case may failed as bug: %s", BUG_URL % exist_bug)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        if prepare_net:
            virsh.net_destroy(net_name)
