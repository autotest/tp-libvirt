import os
import aexpect
from autotest.client import utils
from autotest.client.shared import error
from virttest import virsh
from virttest import utils_test
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.staging import service
from virttest.test_setup import PciAssignable
from virttest import utils_misc


def run(test, params, env):
    """
    Test for PCI multifunction device passthrough to libvirt guest.

    a). NIC:
        1. Get params.
        2. Get the pci device function.
        3. Attach each pci device function to guest.
        4. Start guest and set the ip to all the functions.
        5. Ping to server_ip from ecah function
           to verify the new network device.
    """
    # get the params from params
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    device_type = params.get("libvirt_pci_device_type", "NIC")
    device_name = params.get("libvirt_pci_net_dev_name", "")

    if device_type == "NIC":
        pci_id = params.get("libvirt_pci_net_dev_label")

    net_ip = params.get("libvirt_pci_net_ip", "ENTER.YOUR.IP")
    server_ip = params.get("libvirt_pci_server_ip",
                           "ENTER.YOUR.SERVER.IP")
    netmask = params.get("libvirt_pci_net_mask", "ENTER.YOUR.Mask")

    # Check the parameters from configuration file.
    if (pci_id.count("ENTER")):
        raise error.TestNAError("Please enter your device name for test.")

    if (device_type == "NIC" and (net_ip.count("ENTER") or
                                  server_ip.count("ENTER") or
                                  netmask.count("ENTER"))):
        raise error.TestNAError("Please enter the ips for NIC test.")
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    bus_info = []
    if device_type == "NIC":
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        output = session.cmd_output("ifconfig -a|grep Ethernet")
        nic_list_before = output.splitlines()
    obj = PciAssignable()
    # get all functions id's
    pci_ids = obj.get_same_group_devs(pci_id)
    pci_devs = []
    for val in pci_ids:
        temp = val.replace(":", "_")
        pci_devs.extend(["pci_"+temp])
    for val in pci_devs:
        val = val.replace(".", "_")
        pci_xml = NodedevXML.new_from_dumpxml(val)
        pci_address = pci_xml.cap.get_address_dict()
        vmxml.add_hostdev(pci_address)

    try:
        vmxml.sync()
        vm.start()
        session = vm.wait_for_login()
        if device_type == "NIC":
            output = session.cmd_output("ifconfig -a|grep Ethernet")
            nic_list_after = output.splitlines()
            if nic_list_after == nic_list_before:
                raise error.TestFail(
                    "No Ethernet found for the pci device in guest.")
        # check all functions get same bus and slot
        output = session.cmd_output("lspci -nn | grep %s" % device_name)
        nic_list = output.splitlines()
        for i in range(len(nic_list)):
            bus_info.append(str(nic_list[i]).split(' ', 1)[0])
            nic_list[i] = str(nic_list[i]).split(' ', 1)[0][:-2]
        if len(set(nic_list)) != 1:
            raise error.TestFail(
                "allocates different slots for same adapter")
        # ping to server from each function
        for val in bus_info:
            nic_name = str(utils_misc.get_interface_from_pci_id(val, session))
            try:
                session.cmd("ip addr flush dev %s" % nic_name)
                session.cmd("ip addr add %s/%s dev %s"
                            % (net_ip, netmask, nic_name))
                session.cmd("ip link set %s up" % nic_name)
                session.cmd("ping -I %s %s -c 5" % (nic_name, server_ip))
                break
            except aexpect.ShellError, detail:
                raise error.TestFail("Succeed to set ip on guest, but failed "
                                     "to ping server ip from guest.\n"
                                     "Detail: %s.", detail)
    finally:
        backup_xml.sync()
