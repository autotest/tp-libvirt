import re
import logging
import netaddr
from virttest import utils_test
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.test_setup import PciAssignable
from virttest import utils_misc


def run(test, params, env):
    """
    Test for PCI device passthrough to libvirt guest.

    a). NIC:
        1. Get params.
        2. Get the pci device for specific net_name.
        3. Attach pci device to guest.
        4. Start guest and set the ip to all the physical functions.
        5. Ping to server_ip from each physical function
           to verify the new network device.
    b). STORAGE:
        1. Get params.
        2. Get the pci device for specific storage_dev_name.
        3. Store the result of 'fdisk -l' on guest.
        3. Attach pci device to guest.
        4. Start guest and get the result of 'fdisk -l' on guest.
        5. Compare the result of 'fdisk -l' before and after
            attaching storage pci device to guest.
    """

    # get the params from params
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    sriov = ('yes' == params.get("libvirt_pci_SRIOV", 'no'))
    device_type = params.get("libvirt_pci_device_type", "NIC")
    pci_dev = None
    device_name = None
    pci_address = None
    bus_info = []
    if device_type == "NIC":
        pci_dev = params.get("libvirt_pci_net_dev_label")
        device_name = params.get("libvirt_pci_net_dev_name", "None")
    else:
        pci_dev = params.get("libvirt_pci_storage_dev_label")

    net_ip = params.get("libvirt_pci_net_ip", "ENTER.YOUR.IP")
    server_ip = params.get("libvirt_pci_server_ip",
                           "ENTER.YOUR.SERVER.IP")
    netmask = params.get("libvirt_pci_net_mask", "ENTER.YOUR.Mask")

    # Check the parameters from configuration file.
    if (pci_dev.count("ENTER")):
        test.cancel("Please enter your device name for test.")
    if (device_type == "NIC" and (net_ip.count("ENTER") or
                                  server_ip.count("ENTER") or
                                  netmask.count("ENTER"))):
        test.cancel("Please enter the ips and netmask for NIC "
                    "test in config file")
    fdisk_list_before = None
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    if device_type == "NIC":
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        output = session.cmd_output("lspci -nn")
        nic_list_before = output.splitlines()
        if sriov:
            # The SR-IOV setup of the VF's should be done by test_setup
            # based on the driver options.
            # Usage of the PciAssignable for setting up of the VF's
            # is generic, and eliminates the need to hardcode the driver
            # and number of VF's to be created.

            sriov_setup = PciAssignable(
                driver=params.get("driver"),
                driver_option=params.get("driver_option"),
                host_set_flag=params.get("host_set_flag", 1),
                vf_filter_re=params.get("vf_filter_re"),
                pf_filter_re=params.get("pf_filter_re"),
                pa_type=params.get("pci_assignable"))

            # For Infiniband Controllers, we have to set the link
            # for the VF's before pass-through.
            cont = sriov_setup.get_controller_type()
            if cont == "Infiniband controller":
                sriov_setup.set_linkvf_ib()

            # Based on the PF Device specified, all the VF's
            # belonging to the same iommu group, will be
            # pass-throughed to the guest.
            pci_id = pci_dev.replace("_", ".").strip("pci.").replace(".", ":", 2)
            pci_ids = sriov_setup.get_same_group_devs(pci_id)
            pci_devs = []
            for val in pci_ids:
                temp = val.replace(":", "_")
                pci_devs.extend(["pci_"+temp])
            pci_id = re.sub('[:.]', '_', pci_id)
            for val in pci_devs:
                val = val.replace(".", "_")
                # Get the virtual functions of the pci devices
                # which was generated above.
                pci_xml = NodedevXML.new_from_dumpxml(val)
                virt_functions = pci_xml.cap.virt_functions
                if not virt_functions:
                    test.fail("No Virtual Functions found.")
                for val in virt_functions:
                    pci_dev = utils_test.libvirt.pci_label_from_address(val,
                                                                        radix=16)
                    pci_xml = NodedevXML.new_from_dumpxml(pci_dev)
                    pci_address = pci_xml.cap.get_address_dict()
                    vmxml.add_hostdev(pci_address)
        else:
            pci_id = pci_dev.replace("_", ".").strip("pci.").replace(".", ":", 2)
            obj = PciAssignable()
            # get all functions id's
            pci_ids = obj.get_same_group_devs(pci_id)
            pci_devs = []
            for val in pci_ids:
                temp = val.replace(":", "_")
                pci_devs.extend(["pci_"+temp])
            pci_id = re.sub('[:.]', '_', pci_id)
            for val in pci_devs:
                val = val.replace(".", "_")
                pci_xml = NodedevXML.new_from_dumpxml(val)
                pci_address = pci_xml.cap.get_address_dict()
                vmxml.add_hostdev(pci_address)

    elif device_type == "STORAGE":
        # Store the result of "fdisk -l" in guest.
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        output = session.cmd_output("fdisk -l|grep \"Disk identifier:\"")
        fdisk_list_before = output.splitlines()

        pci_xml = NodedevXML.new_from_dumpxml(pci_dev)
        pci_address = pci_xml.cap.get_address_dict()
        vmxml.add_hostdev(pci_address)
    try:
        vmxml.sync()
        vm.start()
        session = vm.wait_for_login()
        # The Network configuration is generic irrespective of PF or SRIOV VF
        if device_type == "NIC":
            output = session.cmd_output("lspci -nn")
            nic_list_after = output.splitlines()
            net_ip = netaddr.IPAddress(net_ip)
            if nic_list_after == nic_list_before:
                test.fail("passthrough Adapter not found in guest.")
            else:
                logging.debug("Adapter passthorughed to guest successfully")
            output = session.cmd_output("lspci -nn | grep %s" % device_name)
            nic_list = output.splitlines()
            for val in range(len(nic_list)):
                bus_info.append(str(nic_list[val]).split(' ', 1)[0])
                nic_list[val] = str(nic_list[val]).split(' ', 1)[0][:-2]
            bus_info.sort()
            if not sriov:
                # check all functions get same iommu group
                if len(set(nic_list)) != 1:
                    test.fail("Multifunction Device passthroughed but "
                              "functions are in different iommu group")
            # ping to server from each function
            for val in bus_info:
                nic_name = str(utils_misc.get_interface_from_pci_id(val, session))
                session.cmd("ip addr flush dev %s" % nic_name)
                session.cmd("ip addr add %s/%s dev %s"
                            % (net_ip, netmask, nic_name))
                session.cmd("ip link set %s up" % nic_name)
                # Pinging using nic_name is having issue,
                # hence replaced with IPAddress
                s_ping, o_ping = utils_test.ping(server_ip, count=5,
                                                 interface=net_ip, timeout=30,
                                                 session=session)
                logging.info(o_ping)
                if s_ping != 0:
                    err_msg = "Ping test fails, error info: '%s'"
                    test.fail(err_msg % o_ping)
                # Each interface should have unique IP
                net_ip = net_ip + 1

        elif device_type == "STORAGE":
            # Get the result of "fdisk -l" in guest, and
            # compare the result with fdisk_list_before.
            output = session.cmd_output("fdisk -l|grep \"Disk identifier:\"")
            fdisk_list_after = output.splitlines()
            if fdisk_list_after == fdisk_list_before:
                test.fail("Didn't find the disk attached to guest.")
    finally:
        backup_xml.sync()
        # For SR-IOV , VF's should be cleaned up in the post-processing.
        if sriov:
            sriov_setup.release_devs()
