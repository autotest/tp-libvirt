import time

from virttest import utils_net
from virttest import utils_sriov
from virttest import utils_test
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Attach/detach device of hostdev type with vfio variant driver to/from guest
    """
    def parse_iface_dict(pf_pci):
        """
        Parse interface dictionary from parameters
        """
        mac_addr = utils_net.generate_mac_address_simple()

        vf_pci_addr = utils_sriov.pci_to_addr(vf_pci)
        if params.get('iface_dict'):
            iface_dict = eval(params.get('iface_dict', '{}'))
        else:
            if vf_pci_addr.get('type'):
                del vf_pci_addr['type']
            iface_dict = eval(params.get('hostdev_dict', '{}'))
        driver_dict = eval(params.get("driver_dict", "{}"))
        if driver_dict:
            iface_dict.update(driver_dict)
        managed = params.get("managed")
        if managed:
            iface_dict.update({"manged": managed})
        test.log.debug(f"Iface dict: {iface_dict}")

        return iface_dict

    dev_type = params.get("dev_type", "hostdev_interface")
    device_type = "hostdev" if dev_type == "hostdev_device" else "interface"
    expr_driver = params.get("expr_driver", "mlx5_vfio_pci")
    test_pf = params.get("test_pf", "ens3f0np0")
    pf_pci = utils_sriov.get_pf_pci(test_pf=test_pf)
    iface_number = int(params.get("iface_number", "1"))
    ping_dest = params.get("ping_dest", "www.redhat.com")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    orig_vm_xml = new_xml.copy()
    try:
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'hostdev')

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)
        # FIXME: If the "IP" command is executed immediately after the interface
        # is attached, it will be hung.
        time.sleep(10)

        for idx in range(iface_number):
            test.log.info("TEST_STEP: Attach a hostdev interface/device to VM")
            vf_pci = utils_sriov.get_vf_pci_id(pf_pci, idx)
            iface_dict = parse_iface_dict(vf_pci)
            iface_dev = libvirt_vmxml.create_vm_device_by_type(device_type, iface_dict)
            virsh.attach_device(vm.name, iface_dev.xml, ignore_status=False,
                                debug=True)
            actual_vm_hostdev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
                .devices.by_device_tag(device_type)[idx]
            act_hostdev_dict = actual_vm_hostdev.fetch_attrs()
            test.log.debug(f"Actual XML: {actual_vm_hostdev}")
            if act_hostdev_dict.get("driver")["driver_attr"].get("name") != "vfio":
                test.fail("The hostdev driver is not set to VFIO.")
            libvirt_vfio.check_vfio_pci(vf_pci, exp_driver=expr_driver)
        if utils_test.ping(ping_dest, count=3, timeout=5, session=vm_session):
            test.fail("Failed to ping %s." % ping_dest)

        vm_hostdevs = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag(device_type)
        for vm_dev in vm_hostdevs:
            test.log.info("TEST_STEP: Detach a hostdev interface/device from VM")
            virsh.detach_device(vm.name, vm_dev.xml, debug=True,
                                wait_for_event=True, ignore_status=False)
        vm_hostdevs = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag(device_type)
        if vm_hostdevs:
            test.fail("Got hostdev interface/device(%s) after detaching the "
                      "device!" % vm_hostdevs)
        libvirt_vfio.check_vfio_pci(vf_pci, exp_driver="mlx5_core")
        virsh.reboot(vm.name, ignore_status=False, debug=True)
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm.wait_for_serial_login().close()

    finally:
        orig_vm_xml.sync()
