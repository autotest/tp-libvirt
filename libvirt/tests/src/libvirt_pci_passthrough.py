import logging
import netaddr
import time
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
        3. Attach Physical Function's/Virtual Function's to single guest
        4. Start guest and set the ip to all the functions.
        5. Ping to server_ip from each function
           to verify the new network device.
    b). STORAGE:
        1. Get params.
        2. Get the pci device for specific storage_dev_name.
        3. Store the result of 'fdisk -l' on guest.
        3. Attach pci device to guest.
        4. Start guest and get the result of 'fdisk -l' on guest.
        5. Compare the result of 'fdisk -l' before and after
            attaching storage pci device to guest.
    c). GPU:
        1. Get params.
        2. Get the pci device details for specific_gpu.
        3. Attach the GPU's to single guest
        4. Start guest and check the GPU's.
        5. Execute stress test on the GPU's along with GPU-burn.
    """

    # get the params from params
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    sriov = ('yes' == params.get("libvirt_pci_SRIOV", 'no'))
    device_type = params.get("libvirt_pci_device_type", "NIC")
    vm_vfs = int(params.get("number_vfs", 2))
    pci_address = None
    pci_dev = ''
    bus_info = []
    if ((device_type == "NIC") or (device_type == "GPU")):
        pf_filter = params.get("pf_filter")
    elif device_type == "STORAGE":
        pci_dev = params.get("libvirt_pci_storage_dev_label", "ENTER.YOUR.PCI.LABEL")

    net_ip = params.get("libvirt_pci_net_ip", "ENTER.YOUR.IP")
    server_ip = params.get("libvirt_pci_server_ip",
                           "ENTER.YOUR.SERVER.IP")
    netmask = params.get("libvirt_pci_net_mask", "ENTER.YOUR.Mask")
    stress_type = params.get("stress_type", "stress-ng")
    stress_args = params.get("stress_args", "--cpu 8 --io 4 "
                                            "--vm 2 --vm-bytes 128M "
                                            "--timeout 20s")

    # Check the parameters from configuration file.
    if (pf_filter.count("ENTER") or pci_dev.count("ENTER")):
        test.cancel("Please enter your Adapter details for test.")
    if (device_type == "NIC"):
        if (net_ip.count("ENTER") or server_ip.count("ENTER") or netmask.count("ENTER")):
            test.cancel("Please enter the ips and netmask for NIC "
                        "test in config file")
    fdisk_list_before = None
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    obj = PciAssignable(pf_filter_re=pf_filter)
    if not vm.is_alive():
        vm.start()
    session = vm.wait_for_login()
    pci_list_before = vm.get_pci_devices()
    # get all functions id's
    pci_ids = obj.get_same_group_devs(pf_filter)
    pci_devs = []
    for val in pci_ids:
        temp = val.replace(":", "_")
        pci_devs.extend(["pci_"+temp])
    if ((device_type == "NIC") or (device_type == "GPU")):
        if sriov:
            # The SR-IOV setup of the VF's should be done by test_setup
            # PciAssignable class.
            for pf in pci_ids:
                obj.set_vf(pf, vm_vfs)
                cont = obj.get_controller_type()
                if cont == "Infiniband controller":
                    obj.set_linkvf_ib()
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
        pci_list_after = vm.get_pci_devices()
        if sorted(pci_list_after) == sorted(pci_list_before):
            test.fail("Passthrough Adapter not found in guest.")
        else:
            logging.debug("Adapter passthroughed to guest successfully")
        if device_type == "NIC":
            net_ip = netaddr.IPAddress(net_ip)
            nic_list = list(set(pci_list_after).difference(set(pci_list_before)))
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

        elif device_type == "GPU":
            if session.cmd_status('lspci| grep NVIDIA'):
                test.cancel("There are no NVIDIA based graphics card in the guest to run the test")

            # Wait time for GPU's to be initialized.
            time.sleep(60)

            try:
                vm_stress = utils_test.VMStress(vm, stress_type, params)
                logging.info("Executing Stress test in the guest")
                vm_stress.load_stress_tool()
            except utils_test.StressError as info:
                test.error(info)

            cmd = [
                'git clone https://github.com/wilicc/gpu-burn /tmp/gpuburn',
                'cd /tmp/gpuburn/ && make',
                'cd /tmp/gpu-burn/ && ./gpu_burn 30'
            ]

            for i in range(len(cmd)):
                logging.info("Executing GPU-Burn in the guest")
                status = session.cmd(cmd[i])
                logging.debug(status)

    finally:
        backup_xml.sync()
        # For SR-IOV , VF's should be cleaned up in the post-processing.
        if sriov:
            if obj.get_vfs_count() != 0:
                for pci_pf in pci_ids:
                    obj.set_vf(pci_pf, vf_no="0")
