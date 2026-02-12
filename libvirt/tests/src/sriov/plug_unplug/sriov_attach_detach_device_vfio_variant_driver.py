import time

from virttest import utils_sriov
from virttest import utils_test
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_libvirt import libvirt_vmxml

from provider.sriov import sriov_base
from provider.sriov import sriov_vfio


def run(test, params, env):
    """
    Attach/detach device of hostdev type with vfio variant driver to/from guest
    """
    dev_type = params.get("dev_type", "hostdev_interface")
    device_type = "hostdev" if dev_type == "hostdev_device" else "interface"
    expr_driver = params.get("expr_driver", "mlx5_vfio_pci")
    iface_number = int(params.get("iface_number", "1"))
    ping_dest = params.get("ping_dest")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    # Setup SR-IOV if needed
    need_sriov = "yes" == params.get("need_sriov", "no")
    sriov_test_obj = None
    if need_sriov:
        sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
        pf_pci = sriov_test_obj.pf_pci
        pf_name = sriov_test_obj.pf_name
    else:
        # Fallback for backward compatibility if need_sriov is not set
        test_pf = params.get("test_pf")
        pf_pci = utils_sriov.get_pf_pci(test_pf=test_pf)
        if not pf_pci:
            test.cancel("No SR-IOV capable interface found")
        pf_name = test_pf

    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    orig_vm_xml = new_xml.copy()
    try:
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'hostdev')

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        test.log.debug(f'VMXML of {vm_name} after starting:\n{virsh.dumpxml(vm_name).stdout_text}')
        vm_session = vm.wait_for_serial_login(timeout=240)
        # FIXME: If the "IP" command is executed immediately after the interface
        # is attached, it will be hung.
        time.sleep(10)

        for idx in range(iface_number):
            test.log.info("TEST_STEP: Attach a hostdev interface/device to VM")
            vf_pci = utils_sriov.get_vf_pci_id(pf_pci, idx)
            iface_dict = sriov_vfio.parse_iface_dict(vf_pci, params)
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

        test.log.info("TEST_STEP: ping test from VM to outside")
        if pf_name and sriov_vfio.is_linked(pf_name=pf_name) and ping_dest:
            if utils_test.ping(ping_dest, count=3, timeout=5, session=vm_session):
                test.fail("Failed to ping %s." % ping_dest)
        else:
            test.log.debug(f"Skip ping test from VM to outside due to no link to PF ")

        vm_hostdevs = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag(device_type)
        for vm_dev in vm_hostdevs:
            test.log.info("TEST_STEP: Detach a hostdev interface/device from VM")
            virsh.detach_device(vm.name, vm_dev.xml, debug=True,
                                wait_for_event=True, ignore_status=False)
            test.log.debug("Verify: the hostdev interface/device in VM is detached successfully - PASS")
        vm_hostdevs = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag(device_type)
        if vm_hostdevs:
            test.fail("Got hostdev interface/device(%s) after detaching the "
                      "device!" % vm_hostdevs)
        else:
            test.log.debug("Verify: The hostdev interface/device in VM xml is removed successfully - PASS")
        libvirt_vfio.check_vfio_pci(vf_pci, exp_driver="mlx5_core")
        test.log.debug("Verify: Check VF driver successfully after detaching hostdev interface/device - PASS")
        virsh.reboot(vm.name, ignore_status=False, debug=True)
        test.log.debug("Verify: VM reboot is successful after detaching hostdev interface/device - PASS")
        vm.wait_for_serial_login(recreate_serial_console=True).close()
    finally:
        orig_vm_xml.sync()
