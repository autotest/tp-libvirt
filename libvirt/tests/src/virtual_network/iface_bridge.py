import os
import logging

from avocado.utils import process

from virttest import utils_net
from virttest import data_dir
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.staging import service
from virttest.libvirt_xml import vm_xml
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
        # This cmd run a long time, so set timeout=240
        result = virsh.iface_bridge(iface_name, br_name, "--no-stp", debug=True, timeout=240)
        if result.exit_status:
            test.fail("Failed to create bridge:\n%s" % result.stderr)

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

    def modify_iface_xml(br_name, nwfilter, vm_name):
        """
        Modify interface xml with the new bridge and the nwfilter

        :param br_name: bridge name
        :param nwfilter: nwfilter name
        :param vm_name: vm name
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_xml = vmxml.get_devices('interface')[0]
        vmxml.del_device(iface_xml)
        iface_xml.source = {'bridge': br_name}
        iface_xml.filterref = iface_xml.new_filterref(name=nwfilter)
        logging.debug("new interface xml is: %s" % iface_xml)
        vmxml.add_device(iface_xml)
        vmxml.sync()

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
                                        session=session)
        if status:
            test.fail("Fail to ping %s from %s" % (dest_ip, src_ip))

    # Get test params
    bridge_name = params.get("bridge_name", "br0")
    filter_name = params.get("filter_name", "vdsm-no-mac-spoofing")
    ping_count = params.get("ping_count", "5")
    ping_timeout = float(params.get("ping_timeout", "10"))
    iface_name = utils_net.get_net_if(state="UP")[0]
    bridge_script = NETWORK_SCRIPT + bridge_name
    iface_script = NETWORK_SCRIPT + iface_name
    iface_script_bk = os.path.join(data_dir.get_tmp_dir(), "iface-%s.bk" % iface_name)

    vms = params.get("vms").split()
    if len(vms) <= 1:
        test.cancel("Need two VMs to test")
    else:
        vm1_name = vms[0]
        vm2_name = vms[1]

    vm1 = env.get_vm(vm1_name)
    vm2 = env.get_vm(vm2_name)

    # Back up the interface script
    process.run("cp %s %s" % (iface_script, iface_script_bk), shell=True)
    # Back up vm xml
    vm1_xml_bak = vm_xml.VMXML.new_from_dumpxml(vm1_name)
    vm2_xml_bak = vm_xml.VMXML.new_from_dumpxml(vm2_name)

    # Stop NetworkManager service
    NM_service = service.Factory.create_service("NetworkManager")
    NM_status = NM_service.status()
    if NM_status is True:
        NM_service.stop()

    # Start network service
    NW_service = service.Factory.create_service("network")
    NW_status = NW_service.status()
    if NW_status is None:
        logging.debug("network service not found")
        if not utils_package.package_install('network-scripts') or \
                not utils_package.package_install('initscripts'):
            test.cancel("Failed to install network service")
    if NW_status is not True:
        logging.debug("network service is not running")
        NW_service.start()

    try:
        create_bridge(bridge_name, iface_name)
        define_nwfilter(filter_name)
        modify_iface_xml(bridge_name, filter_name, vm1_name)

        if vm1.is_alive():
            vm1.destroy()

        vm1.start()
        # Check if vm can get ip with the new create bridge
        session1 = session2 = None
        try:
            utils_net.update_mac_ip_address(vm1, timeout=120)
            vm1_ip = vm1.get_address()
        except Exception as errs:
            test.fail("vm1 can't get IP with the new create bridge: %s" % errs)

        # Check guest and host can ping each other
        host_ip = utils_net.get_ip_address_by_interface(bridge_name)
        remote_ip = params.get("remote_ip", "www.baidu.com")
        ping(host_ip, vm1_ip, ping_count, ping_timeout)
        ping(host_ip, remote_ip, ping_count, ping_timeout)
        session1 = vm1.wait_for_login()
        ping(vm1_ip, host_ip, ping_count, ping_timeout, session=session1)
        ping(vm1_ip, remote_ip, ping_count, ping_timeout, session=session1)

        # Start vm2 connect to the same bridge
        modify_iface_xml(bridge_name, filter_name, vm2_name)
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
        ping(vm2_ip, vm1_ip, ping_count, ping_timeout, session=session2)
        ping(vm1_ip, vm2_ip, ping_count, ping_timeout, session=session1)
    finally:
        logging.debug("Start to restore")
        vm1_xml_bak.sync()
        vm2_xml_bak.sync()
        virsh.nwfilter_undefine(filter_name, ignore_status=True)
        if libvirt.check_iface(bridge_name, "exists", "--all"):
            virsh.iface_unbridge(bridge_name, timeout=60, debug=True)
        if os.path.exists(iface_script_bk):
            process.run("mv %s %s" % (iface_script_bk, iface_script), shell=True)
        if os.path.exists(bridge_script):
            process.run("rm -rf %s" % bridge_script, shell=True)
        # reload network configuration
        NW_service.restart()
        # recover NetworkManager
        if NM_status is True:
            NM_service.start()
