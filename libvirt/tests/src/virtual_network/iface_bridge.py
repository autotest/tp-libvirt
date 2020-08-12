import os
import logging

from avocado.utils import process

from virttest import utils_net
from virttest import utils_libvirtd
from virttest import data_dir
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.staging import service
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest import utils_package

NETWORK_SCRIPT = "/etc/sysconfig/network-scripts/ifcfg-"


def run(test, params, env):
    """
    Test bridge support from network

    1) create a linux bridge and connect a physical interface to it
    2) define nwfilter with "vdsm-no-mac-spoofing"
    3) redefine the vm with the new create bridge and filter
    4) check if guest can get public ip after vm start
    5) check if guest and host can ping each other
    6) check if guest and host can ping outside
    7) start another vm connected to the same bridge
    8) check if the 2 guests can ping each other
    """

    def create_bridge(br_name, iface_name):
        """
        Create a linux bridge by virsh cmd:
        1. Stop NetworkManager and Start network service
        2. virsh iface-bridge <iface> <name> [--no-stp]

        :param br_name: bridge name
        :param iface_name: physical interface name
        :return: bridge created or raise exception
        """
        # Make sure the bridge not exist
        if libvirt.check_iface(br_name, "exists", "--all"):
            test.cancel("The bridge %s already exist" % br_name)

        # Create bridge
        utils_package.package_install('tmux')
        cmd = 'tmux -c "ip link add name {0} type bridge; ip link set {1} up;' \
              ' ip link set {1} master {0}; ip link set {0} up;' \
              ' pkill dhclient; sleep 6; dhclient {0}; ifconfig {1} 0"'.format(br_name, iface_name)
        process.run(cmd, shell=True, verbose=True)

    def define_nwfilter(filter_name):
        """
        Define nwfilter vdsm-no-mac-spoofing with content like:
        <filter name='vdsm-no-mac-spoofing' chain='root'>
            <filterref filter='no-mac-spoofing'/>
            <filterref filter='no-arp-mac-spoofing'/>
        </filter>

        :param filter_name: the name of nwfilter
        :return: filter created or raise exception
        """
        filter_uuid = params.get("filter_uuid", "11111111-b071-6127-b4ec-111111111111")
        filter_params = {"filter_name": "vdsm-no-mac-spoofing",
                         "filter_chain": "root",
                         "filter_uuid": filter_uuid,
                         "filterref_name_1": "no-mac-spoofing",
                         "filterref_name_2": "no-arp-mac-spoofing"}
        filter_xml = libvirt.create_nwfilter_xml(filter_params).xml
        # Run command
        result = virsh.nwfilter_define(filter_xml, ignore_status=True, debug=True)
        if result.exit_status:
            test.fail("Failed to define nwfilter with %s" % filter_xml)

    def ping(src_ip, dest_ip, ping_count, timeout, session=None):
        """
        Wrap of ping

        :param src_ip: source address
        :param dest_ip: destination address
        :param ping_count: count of icmp packet
        :param timeout: timeout for the ping command
        :param session: local execution or session to execute the ping command
        :return: ping succeed or raise exception
        """
        status, output = utils_net.ping(dest=dest_ip, count=ping_count,
                                        interface=src_ip, timeout=timeout,
                                        session=session, force_ipv4=True)
        if status:
            test.fail("Fail to ping %s from %s" % (dest_ip, src_ip))

    def check_net_functions(guest_ip, ping_count, ping_timeout, guest_session, host_ip, remote_url, endpoint_ip):
        # make sure host network works well
        # host ping remote url
        ping(host_ip, remote_url, ping_count, ping_timeout)
        # host ping guest
        ping(host_ip, guest_ip, ping_count, ping_timeout)
        # guest ping host
        ping(guest_ip, host_ip, ping_count, ping_timeout, session=guest_session)
        # guest ping remote url
        ping(guest_ip, remote_url, ping_count, ping_timeout, session=guest_session)
        # guest ping endpoint
        ping(guest_ip, endpoint_ip, ping_count, ping_timeout, session=guest_session)

    # Get test params
    bridge_name = params.get("bridge_name", "test_br0")
    filter_name = params.get("filter_name", "vdsm-no-mac-spoofing")
    ping_count = params.get("ping_count", "5")
    ping_timeout = float(params.get("ping_timeout", "10"))
    iface_name = utils_net.get_net_if(state="UP")[0]
    bridge_script = NETWORK_SCRIPT + bridge_name
    iface_script = NETWORK_SCRIPT + iface_name
    iface_script_bk = os.path.join(data_dir.get_tmp_dir(), "iface-%s.bk" % iface_name)
    attach_interface = "yes" == params.get("attach_interface", "no")
    iface_model = params.get("iface_model", "virtio")
    iface_source = eval(params.get("iface_source", "{'bridge':'test_br0'}"))
    iface_type = params.get("iface_type", None)
    iface_target = params.get("iface_target", None)
    iface_alias = params.get("iface_alias", None)
    hotplug = "yes" == params.get("hotplug", "no")

    vms = params.get("vms").split()
    if len(vms) <= 1:
        test.cancel("Need two VMs to test")
    else:
        vm1_name = vms[0]
        vm2_name = vms[1]

    vm1 = env.get_vm(vm1_name)
    vm2 = env.get_vm(vm2_name)

    # Back up the interface script
    process.run("cp %s %s" % (iface_script, iface_script_bk),
                shell=True, verbose=True)
    # Back up vm xml
    vm1_xml_bak = vm_xml.VMXML.new_from_dumpxml(vm1_name)
    vm2_xml_bak = vm_xml.VMXML.new_from_dumpxml(vm2_name)

    # Stop NetworkManager service
    NM_service = service.Factory.create_service("NetworkManager")
    NM_status = NM_service.status()
    if not NM_status:
        NM_service.start()
    mac = utils_net.generate_mac_address_simple()

    try:
        create_bridge(bridge_name, iface_name)
        define_nwfilter(filter_name)
        if hotplug:
            err_msgs = ("No more available PCI slots",
                        "No more available PCI addresses")
            # delete the original interface on the vm before hot-plug
            if vm1.is_alive():
                vm1.destroy()
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm1_name)
            iface_xml = vmxml.get_devices('interface')[0]
            logging.debug("Delete the original interface")
            vmxml.del_device(iface_xml)
            vmxml.sync()
            vm1.start()
            # do hot-plug
            if attach_interface:
                logging.info("Try to hot-plug interface")
                options = ("%s %s --model %s --mac %s" %
                           (iface_type, iface_source['bridge'],
                            iface_model, mac))
                ret = virsh.attach_interface(vm1_name, options,
                                             ignore_status=True)
            else:
                logging.info("Try to hot-plug device")
                target = str({'dev': iface_target})
                iface_alias = str({'name': iface_alias})
                vm_iface_source = str(iface_source)
                iface_params = {"type": "bridge", "source": vm_iface_source, "filter": filter_name, "mac": mac,
                                'alias': iface_alias, 'target': target, 'model': iface_model}
                attach_xml = interface.Interface(iface_params['type'])
                attach_xml.xml = libvirt.modify_vm_iface(vm1_name, 'get_xml', iface_params)
                ret = virsh.attach_device(vm1_name, attach_xml.xml,
                                          ignore_status=True,
                                          debug=True)
            if ret.exit_status:
                if any([msg in ret.stderr for msg in err_msgs]):
                    test.error("No more pci slots, can't attach more devices")
                else:
                    test.fail("Failed to attach-interface: %s" % ret.stderr.strip())
            else:
                logging.debug("Hot-plug interface or device pass")

        else:
            vm_iface_source = str(iface_source)
            vm1_iface_params = {"type": "bridge", "source": vm_iface_source, "filter": filter_name, "mac": mac}
            libvirt.modify_vm_iface(vm1_name, "update_iface", vm1_iface_params)

            if vm1.is_alive():
                vm1.destroy()

            vm1.start()
        # apply ip address as it may not be initialized
        session1 = session2 = None
        session1 = vm1.wait_for_serial_login()
        utils_net.restart_guest_network(session1)
        output = session1.cmd_output("ifconfig || ip a")
        logging.debug("guest1 ip info %s" % output)

        # Check guest's network function
        host_ip = utils_net.get_ip_address_by_interface(bridge_name)
        remote_url = params.get("remote_ip", "www.google.com")

        try:
            vm1_ip = utils_net.get_guest_ip_addr(session1, mac)
        except Exception as errs:
            test.fail("vm1 can't get IP with the new create bridge: %s" % errs)
        if hotplug:
            # reboot vm1 then check network function to ensure the interface still there and works fine
            logging.info("reboot the vm")
            virsh.reboot(vm1)
            if session1 is None:
                session1 = vm1.wait_for_serial_login()
            ping(vm1_ip, remote_url, ping_count, ping_timeout, session=session1)
            # restart libvirtd service then check the interface still works fine
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
            vm1.cleanup_serial_console()
            vm1.create_serial_console()
            session1 = vm1.wait_for_serial_login()
            ping(vm1_ip, remote_url, ping_count, ping_timeout, session=session1)
            logging.info("after reboot and restart libvirtd, the network works fine")
            # hot-unplug interface/device
            if attach_interface:
                ret = virsh.detach_interface(vm1_name, "bridge",
                                             ignore_status=True)
            else:
                ret = virsh.detach_device(vm1_name, attach_xml.xml,
                                          ignore_status=True,
                                          debug=True)
            if ret.exit_status:
                test.fail("Hot-unplug interface/device fail")
            else:
                logging.info("hot-unplug interface/device succeed")

        else:
            # Start vm2 connect to the same bridge
            mac2 = utils_net.generate_mac_address_simple()
            vm2_iface_params = {"type": "bridge", "source": vm_iface_source, "filter": filter_name, "mac": mac2}
            libvirt.modify_vm_iface(vm2_name, "update_iface", vm2_iface_params)
            if vm2.is_alive():
                vm2.destroy()
            vm2.start()

            # Check if vm1 and vm2 can ping each other
            try:
                utils_net.update_mac_ip_address(vm2, timeout=120)
                vm2_ip = vm2.get_address()
            except Exception as errs:
                test.fail("vm2 can't get IP with the new create bridge: %s" % errs)
            session2 = vm2.wait_for_login()
            # make sure guest has got ip address
            utils_net.restart_guest_network(session2)
            output2 = session2.cmd_output("ifconfig || ip a")
            logging.debug("guest ip info %s" % output2)
            # check 2 guests' network functions
            check_net_functions(vm1_ip, ping_count, ping_timeout, session1,
                                host_ip, remote_url, vm2_ip)
            check_net_functions(vm2_ip, ping_count, ping_timeout, session2,
                                host_ip, remote_url, vm1_ip)

    finally:
        logging.debug("Start to restore")
        vm1_xml_bak.sync()
        vm2_xml_bak.sync()
        virsh.nwfilter_undefine(filter_name, ignore_status=True)
        if libvirt.check_iface(bridge_name, "exists", "--all"):
            virsh.iface_unbridge(bridge_name, timeout=60, debug=True)
        if os.path.exists(iface_script_bk):
            process.run("mv %s %s" % (iface_script_bk, iface_script),
                        shell=True, verbose=True)
        if os.path.exists(bridge_script):
            process.run("rm -rf %s" % bridge_script, shell=True, verbose=True)
        cmd = 'tmux -c "ip link set {1} nomaster;  ip link delete {0};' \
              'pkill dhclient; sleep 6; dhclient {1}"'.format(bridge_name, iface_name)
        process.run(cmd, shell=True, verbose=True)
        # reload network configuration
        NM_service.restart()
        # recover NetworkManager
        if NM_status is True:
            NM_service.start()
