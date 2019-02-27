import re
import logging
import signal

from virttest import utils_net
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import network_xml
from virttest.utils_test import libvirt as utlv
from virttest.compat_52lts import results_stdout_52lts
from avocado.utils import process

from provider import libvirt_version


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
    net_option = params.get("net_option", "")
    status_error = "yes" == params.get("status_error", "no")
    prepare_net = "yes" == params.get("prepare_net", "yes")
    hotplug_iface = "yes" == params.get("hotplug_interface", "no")
    filter_by_mac = "yes" == params.get("filter_by_mac", "no")
    invalid_mac = "yes" == params.get("invalid_mac", "no")
    expect_msg = params.get("leases_err_msg")
    # Generate a random string as the MAC address
    nic_mac = None
    if invalid_mac:
        nic_mac = utils_misc.generate_random_string(17)

    # Command won't fail on old libvirt
    if not libvirt_version.version_compare(1, 3, 1) and invalid_mac:
        logging.debug("Reset case to positive as BZ#1261432")
        status_error = False

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
                leases.append(dict(list(zip(keys, values))))
            return leases
        except Exception:
            test.error("Fail to parse output: %s" % output)

    def get_ip_by_mac(mac_addr, try_dhclint=False, timeout=120):
        """
        Get interface IP address by given MAC addrss. If try_dhclint is
        True, then try to allocate IP addrss for the interface.
        """
        session = vm.wait_for_login(login_nic_index, timeout=timeout, serial=True)

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
                test.fail("Lease info is empty")
            else:
                logging.debug("No dhcp lease info find as expected")
        else:
            if not expected_find:
                test.fail("Find unexpected dhcp lease info: %s"
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
                test.fail("Address '%s' is not expected" % iface_ip)
        if expected_find and not find_mac:
            test.fail("No matched MAC address")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    if vm.is_alive():
        vm.destroy(gracefully=False)
    login_nic_index = 0
    new_nic_index = 0
    # Cleanup dirty dnsmaq, firstly get all network,and destroy all networks except
    # default
    net_state = virsh.net_state_dict(only_names=True)
    logging.debug("current networks: %s, destroy and undefine networks "
                  "except default!", net_state)
    for net in net_state:
        if net != "default":
            virsh.net_destroy(net)
            virsh.net_undefine(net)
    cmd = "ps aux|grep dnsmasq|grep -v grep | grep -v default | awk '{print $2}'"
    pid_list = results_stdout_52lts(process.run(cmd, shell=True)).strip().splitlines()
    logging.debug(pid_list)
    for pid in pid_list:
        utils_misc.safe_kill(pid, signal.SIGKILL)
    # Create new network
    if prepare_net:
        create_network()
    nets = virsh.net_state_dict()
    if net_name not in list(nets.keys()) and not status_error:
        test.error("Not find network '%s'" % net_name)
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
            op = "--type network --model virtio --source %s --mac %s" \
                 % (net_name, iface_mac)
            nic_params = {'mac': iface_mac, 'nettype': 'bridge',
                          'ip_version': 'ipv4'}
            login_timeout = 120
            if not hotplug_iface:
                op += " --config"
                virsh.attach_interface(vm_name, option=op, debug=True,
                                       ignore_status=False)
                vm.add_nic(**nic_params)
                vm.start()
                new_nic_index = vm.get_nic_index_by_mac(iface_mac)
                if new_nic_index > 0:
                    login_nic_index = new_nic_index
            else:
                vm.start()
                # wait for VM start before hotplug interface
                vm.wait_for_serial_login()
                virsh.attach_interface(vm_name, option=op, debug=True,
                                       ignore_status=False)
                vm.add_nic(**nic_params)
                # As VM already started, so the login timeout could be shortened
                login_timeout = 10
            new_interface_ip = get_ip_by_mac(iface_mac, try_dhclint=True,
                                             timeout=login_timeout)
            # Allocate IP address for the new interface may fail, so only
            # check the result if get new IP address
            if new_interface_ip:
                expected_find = True
            result = virsh.net_dhcp_leases(net_name, mac=nic_mac,
                                           debug=False, ignore_status=True)
            utlv.check_exit_status(result, status_error)
            lease = get_net_dhcp_leases(result.stdout.strip())
            check_net_lease(lease, expected_find)
        else:
            if expect_msg:
                utlv.check_result(result, expect_msg.split(';'))
    finally:
        # Delete the new attached interface
        if new_nic_index > 0:
            vm.del_nic(new_nic_index)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        if prepare_net:
            virsh.net_destroy(net_name)
