import logging
import re

from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import nodedev_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def get_device_name(pci_id):
    """
    Get device name from pci_id

    :param pci_id: PCI ID of a device(eg. 0000:05:10.1)
    :return: Name of a device(eg. pci_0000_05_00_1)
    """
    return '_'.join(['pci']+re.split('[.:]', pci_id))


def add_hostdev_device(vm_name, pci):
    """
    Add hostdev device to VM

    :param vm_name: Name of VM
    :param pci_id: PCI ID of a device
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    hostdev_dev = libvirt.create_hostdev_xml(pci)
    vmxml.add_device(hostdev_dev)
    vmxml.sync()


def run(test, params, env):
    """
    Nodedev related test.
    """
    def check_driver_from_xml(dev_name, driver_type='vfio-pci',
                              status_error=False):
        """
        Check driver of a device from nodedev XML

        :param dev_name: Name of a device(eg. pci_0000_05_00_1)
        :param driver_type: Type of a device
        :param status_error: Whether the driver should be same with 'driver_type'
        :raise: TestFail if not match
        """
        dev_xml = nodedev_xml.NodedevXML.new_from_dumpxml(dev_name)
        if status_error == (dev_xml.driver_name != driver_type):
            test.fail("The driver %s should%s be '%s'."
                      % (dev_xml.driver_name, ' not' if status_error else '',
                         driver_type))

    def nodedev_test(dev_name, status_error=False):
        """
        Execute virsh nodedev-* commands

        :param dev_name: Name of a device(eg. pci_0000_05_00_1)
        :param status_error: Whether the command should be failed
        """
        res = virsh.nodedev_reset(dev_name, debug=True)
        libvirt.check_exit_status(res, status_error)
        res = virsh.nodedev_detach(dev_name, debug=True)
        libvirt.check_exit_status(res, status_error)
        res = virsh.nodedev_reattach(dev_name, debug=True)
        libvirt.check_exit_status(res, status_error)

    def check_hostdev_device(vm_name):
        """
        Check hostdev device from VM's XML

        :param vm_name: VM's name
        :raise: TestFail if the hoodev device is not found
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        check_hostdev = vm_xml.VMXML.new_from_dumpxml(vm_name)\
            .devices.by_device_tag('hostdev')
        if not check_hostdev:
            test.fail("The hostdev device does not exist: %s."
                      % check_hostdev)

    def test_pf():
        """
        Reattach/reset/detach a pci device when it is used in guest

        1) Detach/Reset/reattach the device
        2) Add the device to VM
        3) Start the VM
        4) Check driver of the device
        5) Detach/Reset/reattach the device again
        """
        dev_name = get_device_name(pf_pci)
        check_driver_from_xml(dev_name)
        nodedev_test(dev_name)
        add_hostdev_device(vm_name, pf_pci)
        vm.start()
        check_hostdev_device(vm_name)
        check_driver_from_xml(dev_name, status_error=True)
        nodedev_test(dev_name, True)

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    pf_pci = utils_sriov.get_pf_pci()
    if not pf_pci:
        test.cancel("NO available pf found.")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        run_test()

    finally:
        logging.info("Recover test enviroment.")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        orig_config_xml.sync()
