import time
from virttest import utils_disk
from virttest import utils_net

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.sriov import sriov_base
from provider.viommu import viommu_base

from provider.sriov import check_points as sriov_check_points


def run(test, params, env):
    """
    Start vm with iommu device and kinds of virtio devices with iommu=on, and
    check network and disk function.
    """
    cleanup_ifaces = "yes" == params.get("cleanup_ifaces", "yes")
    ping_dest = params.get('ping_dest')
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    test_devices = eval(params.get("test_devices", "[]"))
    dev_in_same_iommu_group = eval(params.get("dev_in_same_iommu_group", "[]"))
    need_sriov = "yes" == params.get("need_sriov", "no")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sroiv_test_obj = None

    test_obj = viommu_base.VIOMMUTest(vm, test, params)
    if need_sriov:
        sroiv_test_obj = sriov_base.SRIOVTest(vm, test, params)

    try:
        test_obj.setup_iommu_test(iommu_dict=iommu_dict, cleanup_ifaces=cleanup_ifaces)
        test_obj.prepare_controller()
        vm.start()
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(
            timeout=int(params.get('login_timeout')))
        pre_devices = viommu_base.get_devices_pci(vm_session, test_devices)
        vm_session.close()
        vm.destroy()

        for dev in ["disk", "video"]:
            dev_dict = eval(params.get('%s_dict' % dev, '{}'))
            if dev == "disk" and dev_dict:
                dev_dict = test_obj.update_disk_addr(dev_dict)
                test.log.debug(f"disk_addr_updated:{dev_dict}")
                if dev_dict["target"].get("bus") != "virtio":
                    libvirt_vmxml.modify_vm_device(
                            vm_xml.VMXML.new_from_dumpxml(vm.name), dev, {'driver': None})

            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name), dev, dev_dict)
        test_obj.log_controller_dicts()
        if need_sriov:
            iface_dicts = sroiv_test_obj.parse_iface_dict()
            test.log.debug(iface_dicts)
            test_obj.params["iface_dict"] = str(sroiv_test_obj.parse_iface_dict())
        test_obj.log_controller_dicts()
        iface_dict = test_obj.parse_iface_dict()
        test_obj.log_controller_dicts()

        if cleanup_ifaces:
            # Handle both single dict and list of dicts
            if isinstance(iface_dict, list):
                libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
                for single_iface_dict in iface_dict:
                    dev_obj = libvirt_vmxml.create_vm_device_by_type("interface", single_iface_dict)
                    test.log.debug(f"XML of interface device is:\n{dev_obj}")
                    libvirt.add_vm_device(
                            vm_xml.VMXML.new_from_dumpxml(vm.name),
                            dev_obj)
            else:
                libvirt_vmxml.modify_vm_device(
                        vm_xml.VMXML.new_from_dumpxml(vm.name),
                        "interface", iface_dict)

        test.log.info("TEST_STEP: Start the VM.")
        vm.start()
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(
            timeout=int(params.get('login_timeout')))
        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))

        test.log.info("TEST_STEP: Check dmesg message about iommu inside the vm.")
        vm_session.cmd("dmesg | grep -i 'Adding to iommu group'")
        viommu_base.check_vm_iommu_group(vm_session, test_devices, pre_devices)
        if dev_in_same_iommu_group:
            devices_pci_info = viommu_base.get_devices_pci(vm_session, dev_in_same_iommu_group)
            devices_pci = [x for y in devices_pci_info.values() for x in y]
            dev_dir = str(viommu_base.get_iommu_dev_dir(vm_session, devices_pci[0]))
            for dev in devices_pci:
                vm_session.cmd(f"ls {dev_dir} |grep {dev}")

        test.log.info("TEST_STEP: Check if the VM disk and network are woring well.")
        utils_disk.dd_data_to_vm_disk(vm_session, "/mnt/test")
        if need_sriov:
            sriov_check_points.check_vm_network_accessed(vm_session, ping_dest=ping_dest)
        else:
            s, o = utils_net.ping(ping_dest, count=5, timeout=10, session=vm_session)
            if s:
                test.fail("Failed to ping %s! status: %s, output: %s." % (ping_dest, s, o))
    finally:
        if 'vm_session' in locals():
            test.log.debug("Closing vm_session")
            vm_session.close()
        vm.cleanup_serial_console()
        test.log.debug("------------------------------------------------------------")
        time.sleep(60)
        test_obj.teardown_iommu_test()
