import os
import re
import logging
import aexpect
from avocado.utils import process
from avocado.utils import distro
from virttest import virsh
from virttest import utils_test
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.staging import service
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
        test.cancel("Please enter the ips and netmask for NIC test in config file")
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
            # set the parameter max_vfs of igb module to 7. Then we can use
            # the virtual function pci device for network device.

            # command 'modprobe -r igb' to unload igb module
            # command '&& modprobe igb max_vfs=7' to load it again
            #          with max_vfs=7
            # command '|| echo 'FAIL' > output_file' is a flag to mean
            #          modprobe igb with max_vfs=7 failed.
            # command '|| modprobe igb' is a handler of error occured
            #          when we load igb again. If command 2 failed,
            #          this command will be executed to recover network.
            output_file = os.path.join(test.tmpdir, "output")
            if os.path.exists(output_file):
                os.remove(output_file)
            mod_cmd = ("modprobe -r igb && modprobe igb max_vfs=7 ||"
                       "echo 'FAIL' > %s && modprobe igb &" % output_file)
            result = process.run(mod_cmd, ignore_status=True, shell=True)
            if os.path.exists(output_file):
                test.error("Failed to modprobe igb with max_vfs=7.")
            # Get the virtual function pci device which was generated above.
            pci_xml = NodedevXML.new_from_dumpxml(pci_dev)
            virt_functions = pci_xml.cap.virt_functions
            if not virt_functions:
                test.error("Init virtual function failed.")
            pci_address = virt_functions[0]
            pci_dev = utils_test.libvirt.pci_label_from_address(pci_address,
                                                                radix=16)
            # Find the network name (ethX) is using this pci device.
            distro_details = distro.detect()
            if distro_details.name == 'Ubuntu':
                network_service = service.Factory.create_service("networking")
            else:
                network_service = service.Factory.create_service("network")
            network_service.restart()
            result = virsh.nodedev_list("net")
            nodedev_nets = result.stdout.strip().splitlines()
            device = None
            for nodedev in nodedev_nets:
                netxml = NodedevXML.new_from_dumpxml(nodedev)
                if netxml.parent == pci_dev:
                    device = nodedev
                    break
            if not device:
                test.error("There is no network name is using "
                           "Virtual Function PCI device %s." %
                           pci_dev)
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
        if device_type == "NIC":
            output = session.cmd_output("lspci -nn")
            nic_list_after = output.splitlines()
            if nic_list_after == nic_list_before:
                test.fail("passthrough Adapter not found in guest.")
            else:
                logging.debug("Adapter passthorughed to guest successfully")
            if sriov:
                try:
                    output = session.cmd_output("lspci -nn | grep %s" % device_name)
                    nic_id = str(output).split(' ', 1)[0]
                    nic_name = str(utils_misc.get_interface_from_pci_id(nic_id, session))
                    session.cmd("ip addr flush dev %s" % nic_name)
                    session.cmd("ip addr add %s/%s dev %s"
                                % (net_ip, netmask, nic_name))
                    session.cmd("ip link set %s up" % nic_name)
                    session.cmd("ping -I %s %s -c 5" % (nic_name, server_ip))
                except aexpect.ShellError, detail:
                    test.error("Succeed to set ip on guest, but failed "
                               "to ping server ip from guest. %s \n" % detail)
            else:
                output = session.cmd_output("lspci -nn | grep %s" % device_name)
                nic_list = output.splitlines()
                for val in range(len(nic_list)):
                    bus_info.append(str(nic_list[val]).split(' ', 1)[0])
                    nic_list[val] = str(nic_list[val]).split(' ', 1)[0][:-2]
                # check all functions get same iommu group
                if len(set(nic_list)) != 1:
                    test.fail("Multifunction Device passthroughed but "
                              "functions are in different iommu group")
                # ping to server from each function
                bus_info.sort()
                for val in bus_info:
                    nic_name = str(utils_misc.get_interface_from_pci_id(val, session))
                    try:
                        session.cmd("ip addr flush dev %s" % nic_name)
                        session.cmd("ip addr add %s/%s dev %s"
                                    % (net_ip, netmask, nic_name))
                        session.cmd("ip link set %s up" % nic_name)
                        session.cmd("ping -I %s %s -c 5" % (nic_name, server_ip))
                    except aexpect.ShellError, detail:
                        test.error("Succeed to set ip on guest, but failed "
                                   "to ping server ip from guest. %s\n" % detail)
        elif device_type == "STORAGE":
            # Get the result of "fdisk -l" in guest, and compare the result with
            # fdisk_list_before.
            output = session.cmd_output("fdisk -l|grep \"Disk identifier:\"")
            fdisk_list_after = output.splitlines()
            if fdisk_list_after == fdisk_list_before:
                test.fail("Didn't find the disk attached to guest.")
    finally:
        backup_xml.sync()
