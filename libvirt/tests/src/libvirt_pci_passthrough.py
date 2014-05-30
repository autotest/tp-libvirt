import os

from autotest.client import utils
from autotest.client.shared import error
from virttest import virsh, utils_test, aexpect
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.staging import service


def run(test, params, env):
    """
    Test for PCI device passthrough to libvirt guest.

    a). NIC:
        1. Get params.
        2. Get the pci device for specific net_name.
        3. Attach pci device to guest.
        4. Start guest and set the ip of guest.
        5. Ping the server_ip of from guest
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

    net_name = params.get("libvirt_pci_net_name", "eth0")
    server_ip = params.get("libvirt_pci_server_ip")

    storage_dev_name = params.get("libvirt_pci_storage_dev_name", "/dev/sdb")
    fdisk_list_before = None

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    pci_address = None
    if device_type == "NIC":
        # Find the pci device for given network device.
        result = virsh.nodedev_list(cap="net")
        nodedev_nets = result.stdout.strip().splitlines()
        device = None
        for nodedev in nodedev_nets:
            netxml = NodedevXML.new_from_dumpxml(nodedev)
            if netxml.cap.interface == net_name:
                device = nodedev
                break
        if not device:
            raise error.TestNAError("There is no network device name of %s." %
                                    net_name)
        pci_dev = netxml.parent

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
            result = utils.run(mod_cmd, ignore_status=True)
            if os.path.exists(output_file):
                raise error.TestError("Failed to modprobe igb with max_vfs=7.")
            # Get the virtual function pci device which was generated above.
            pci_xml = NodedevXML.new_from_dumpxml(pci_dev)
            virt_functions = pci_xml.cap.virt_functions
            if not virt_functions:
                raise error.TestError("Init virtual function failed.")
            pci_address = virt_functions[0]
            pci_dev = utils_test.libvirt.pci_label_from_address(pci_address,
                                                                radix=16)

            # Find the network name (ethX) is using this pci device.
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
                raise error.TestNAError("There is no network name is using "
                                        "Virtual Function PCI device %s." %
                                        pci_dev)

        pci_xml = NodedevXML.new_from_dumpxml(pci_dev)
        pci_address = pci_xml.cap.get_address_dict()

    elif device_type == "STORAGE":
        # Store the result of "fdisk -l" in guest.
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        output = session.cmd_output("fdisk -l|grep \"Disk identifier:\"")
        fdisk_list_before = output.splitlines()

        result = virsh.nodedev_list(cap="storage")
        nodedev_storages = result.stdout.strip().splitlines()
        device = None
        for nodedev in nodedev_storages:
            storage_xml = NodedevXML.new_from_dumpxml(nodedev)
            if storage_xml.cap.block == storage_dev_name:
                device = nodedev
                break
        if not device:
            raise error.TestNAError("There is no block device name of %s." %
                                    storage_dev_name)
        pci_xml = NodedevXML.new_from_dumpxml(storage_xml.parent)

        # In some cases, the parent of target storage device might not be
        # a PCI device, but is of type 'scsi' for example.
        # SKIP these tests with a proper message.
        if pci_xml.cap_type != 'pci':
            raise error.TestNAError("The parent node device of the storage "
                                    "device need to be a PCI device. "
                                    "But parent of %s is a %s device." %
                                    (storage_dev_name, pci_xml.cap_type))
        pci_address = pci_xml.cap.get_address_dict()

    vmxml.add_hostdev(pci_address)

    try:
        vmxml.sync()
        vm.start()
        session = vm.wait_for_login()
        if device_type == "NIC":
            try:
                session.cmd("ping -c 4 %s" % server_ip)
            except aexpect.ShellError, detail:
                raise error.TestFail("Succeed to set ip on guest, but failed "
                                     "to ping server ip from guest.\n"
                                     "Detail: %s.", detail)
        elif device_type == "STORAGE":
            # Get the result of "fdisk -l" in guest, and compare the result with
            # fdisk_list_before.
            output = session.cmd_output("fdisk -l|grep \"Disk identifier:\"")
            fdisk_list_after = output.splitlines()
            if fdisk_list_after == fdisk_list_before:
                raise error.TestFail("Didn't find the disk attached to guest.")
    finally:
        backup_xml.sync()
