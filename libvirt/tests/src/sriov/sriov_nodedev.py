import logging

from provider.sriov import sriov_base

from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import nodedev_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


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


def add_hostdev_iface(vm, vf_pci):
    """
    Add hostdev device to VM

    :param vm: VM object
    :param vf_pci: PCI ID of a VF
    """
    libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
    iface_dict = {"type": "hostdev", "managed": "yes",
                  "hostdev_addr": str(utils_sriov.pci_to_addr(vf_pci))}
    libvirt.modify_vm_iface(vm.name, "update_iface", iface_dict)


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

    def nodedev_test(dev_name, status_error=False, no_reset=False):
        """
        Execute virsh nodedev-* commands

        :param dev_name: Name of a device(eg. pci_0000_05_00_1)
        :param status_error: Whether the command should be failed
        :param no_reset: Whether reset nodedev
        """
        if not no_reset:
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

    def check_hostdev_iface(vm_name):
        """
        Check hostdev interface in VM

        :param vm_name: Name of VM
        :raise: TestFail if not found
        """
        vm_ifaces = [iface.get_type_name() for iface in vm_xml.VMXML.
                     new_from_dumpxml(vm_name).devices.
                     by_device_tag("interface")]
        if 'hostdev' not in vm_ifaces:
            test.fail("hostdev interface does not exist: %s." % vm_ifaces)

    def compare_vf_mac(pf_name, exp_vf_mac):
        """
        Compare the current vf's mac address with exp_vf_mac

        :param pf_name: The PF's
        :param exp_vf_mac: The expected vf's mac address
        :raise: TestFail if not match
        """
        logging.debug("VF's mac should be %s.", exp_vf_mac)
        vf_mac_act = utils_sriov.get_vf_mac(pf_name, is_admin=False)
        if exp_vf_mac != vf_mac_act:
            test.fail("MAC address changed from '%s' to '%s' after reattaching "
                      "vf." % (exp_vf_mac, vf_mac_act))

    def test_pf():
        """
        Reattach/reset/detach a pci device when it is used in guest

        1) Detach/Reset/reattach the device
        2) Add the device to VM
        3) Start the VM
        4) Check driver of the device
        5) Detach/Reset/reattach the device again
        """
        dev_name = utils_sriov.get_device_name(pf_pci)
        check_driver_from_xml(dev_name)
        nodedev_test(dev_name)
        add_hostdev_device(vm_name, pf_pci)
        vm.start()
        check_hostdev_device(vm_name)
        check_driver_from_xml(dev_name, status_error=True)
        nodedev_test(dev_name, True)

    def test_vf():
        """
        Detach/Reattach a vf when it is used in guest

        1) Detach/reattach the device
        2) Add the device to VM
        3) Start the VM
        4) Check driver of the device
        5) Detach/reattach the device again
        """
        logging.info("Initialize the vfs.")
        sriov_base.setup_vf(pf_pci, params)
        vf_pci = utils_sriov.get_vf_pci_id(pf_pci)
        pf_name = utils_sriov.get_pf_info_by_pci(pf_pci).get('iface')
        vf_mac = utils_sriov.get_vf_mac(pf_name, is_admin=False)
        logging.debug("VF's mac: %s.", vf_mac)

        logging.info("Check the vf's driver, it should not be vfio-pci.")
        dev_name = utils_sriov.get_device_name(vf_pci)
        check_driver_from_xml(dev_name)

        logging.info("Detach and reattach the device and check vf's mac.")
        nodedev_test(dev_name, no_reset=True)
        compare_vf_mac(pf_name, vf_mac)

        logging.info("Cold-plug the device into the VM.")
        add_hostdev_iface(vm, vf_pci)
        vm.start()
        check_hostdev_iface(vm.name)

        logging.info("Check the device info. It should be vfio-pci.")
        check_driver_from_xml(dev_name, status_error=True)
        nodedev_test(dev_name, True)

        logging.info("Destroy the vm, and check the vf's mac is recovered.")
        vm.destroy(gracefully=False)
        compare_vf_mac(pf_name, vf_mac)

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
        sriov_base.recover_vf(pf_pci, params)
