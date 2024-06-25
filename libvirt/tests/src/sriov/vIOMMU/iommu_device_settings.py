from virttest import utils_disk
from virttest import utils_net

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

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

        for dev in ["disk", "video"]:
            dev_dict = eval(params.get('%s_dict' % dev, '{}'))
            if dev == "disk":
                dev_dict = test_obj.update_disk_addr(dev_dict)

            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name), dev, dev_dict)
        if need_sriov:
            iface_dicts = sroiv_test_obj.parse_iface_dict()
            test.log.debug(iface_dicts)
            test_obj.params["iface_dict"] = str(sroiv_test_obj.parse_iface_dict())
        iface_dict = test_obj.parse_iface_dict()

        if cleanup_ifaces:
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
        viommu_base.check_vm_iommu_group(vm_session, test_devices)

        test.log.info("TEST_STEP: Check if the VM disk and network are woring well.")
        utils_disk.dd_data_to_vm_disk(vm_session, "/mnt/test")
        if need_sriov:
            sriov_check_points.check_vm_network_accessed(vm_session, ping_dest=ping_dest)
        else:
            s, o = utils_net.ping(ping_dest, count=5, timeout=10, session=vm_session)
            if s:
                test.fail("Failed to ping %s! status: %s, output: %s." % (ping_dest, s, o))
    finally:
        test_obj.teardown_iommu_test()
