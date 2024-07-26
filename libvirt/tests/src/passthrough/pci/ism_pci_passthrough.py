import logging as log
import time

from threading import Thread

from avocado.core.exceptions import TestError, TestFail
from avocado.utils import process
from virttest import virsh
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.nodedev_xml import NodedevXML

from provider.vfio import get_hostdev_xml


logging = log.getLogger("avocado." + __name__)
smc_port = 37373


def get_ism_address(pci_dev):
    """
    Get the ISM device address.

    :param pci_dev: libvirt's node device name
    :return: the address element
    :raises TestError: if the device is not an ISM device
    """
    pci_xml = NodedevXML.new_from_dumpxml(pci_dev)
    if "ism" != pci_xml.driver_name:
        raise TestError("Device %s is not an ISM device: %s" % (pci_dev, pci_xml))
    return pci_xml.cap.get_address_dict()


def check_device_is_available(vm):
    """
    Checks that the device is available inside the VM.

    :param vm: the guest instance
    """
    session = vm.wait_for_login()
    output = session.cmd_output("lspci")
    session.close()
    devices = output.split("\n")
    if not len(devices) >= 1 or "ISM" not in devices[0]:
        raise TestFail("Expected 1 ISM PCI device but got: %s" % output)


def check_guest_and_host_can_communicate(vm, guest_iface):
    """
    Checks that the guest and the host can communicate
    using the smc_chk tool.

    :param vm: the guest instance
    :param guest_iface: the guest's interface name, e.g. enc1
    """
    session = vm.wait_for_login()

    thread = start_test_server(session)
    guest_ip = vm.wait_for_get_address(nic_index=0)
    process.run("systemctl stop firewalld")
    output = process.run("smc_chk -C %s -p %s" % (guest_ip, smc_port)).stdout_text
    thread.join()
    session.close()

    if "Success, using SMC-D" not in output:
        raise TestFail("SMC-D is not functional: %s" % output)


def hotplug_device(pci_dev, vm_name):
    """
    Hotplug the device

    :param vm_name: the VM name
    :param pci_dev: the node device name
    """
    pci_address = get_ism_address(pci_dev)
    hostdev_xml = get_hostdev_xml(pci_address)
    virsh.attach_device(vm_name, hostdev_xml.xml, flagstr="--live", debug=True)
    time.sleep(1)


def coldplug_device(pci_dev, vmxml):
    """
    Updates the VMXML with the given PCI device if it's an ISM device.

    :param pci_dev: The node device name of the ISM device.
    :param vmxml: VMXML instance of the VM
    """
    pci_address = get_ism_address(pci_dev)
    vmxml.add_hostdev(pci_address)
    vmxml.sync()


def start_test_server(session):
    """
    Starts the server part in a different thread.

    :return: the thread for cleanup
    """
    session.cmd("systemctl stop firewalld")

    server = Thread(
        target=lambda: session.cmd("timeout 10s smc_chk -S -p %s" % smc_port)
    )
    server.start()
    time.sleep(1)
    return server


def check_device_is_available_after_reboot(vm):
    """
    Checks that the device is available after reboot

    :param vm: the guest instance
    """
    vm.reboot()
    check_device_is_available(vm)


def check_vm_can_start_after_reboot(vm):
    """
    Checks that the VM can be started after reboot-destroy

    :param vm: the guest instance
    """
    time.sleep(1)
    vm.reboot()
    time.sleep(1)
    vm.destroy()
    time.sleep(1)
    try:
        vm.start()
    except:
        raise TestFail("Can't start VM after reboot-destroy")


def run(test, params, env):
    """
    Test for ISM device passthrough to libvirt guest.
    """
    # get the params from params
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pci_dev = params.get("pci_dev", "pci_0000_00_00_0")
    check = params.get("check")
    guest_iface = params.get("guest_iface")

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        if "hotplug" not in check:
            if vm.is_alive():
                vm.destroy()
            coldplug_device(pci_dev, vmxml)
            vm.start()

        if check == "available_hotplug":
            hotplug_device(pci_dev, vm_name)
            check_device_is_available(vm)
        elif check == "available":
            check_device_is_available(vm)
        elif check == "available_after_reboot":
            check_device_is_available_after_reboot(vm)
        elif check == "start_after_reboot":
            check_vm_can_start_after_reboot(vm)
        elif check == "smc_functional":
            check_guest_and_host_can_communicate(vm, guest_iface)

    finally:
        process.run("systemctl start firewalld", shell=True)
        session = vm.wait_for_login()
        session.close()
        vm.destroy()
        backup_xml.sync()
