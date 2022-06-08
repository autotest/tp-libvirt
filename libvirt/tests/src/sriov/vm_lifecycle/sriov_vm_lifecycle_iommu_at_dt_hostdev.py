from provider.sriov import sriov_base
from provider.sriov import check_points

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test the hostdev interface works well with the vIOMMU enabled.
    """
    def run_test():
        """
        Start vm with IOMMU device and hostdev interface/device, hotunplug and
        hotplug

        1. Start vm with iommu enabled, and guest kernel cmd line with iommu
        enabled
        2. Test network connectivity. for failover, also check the interface
        quantity on the vm.
        3. Hotunplug the hostdev interface/device, check the device is
        removed from the live xml, and the interface on the vm
        4. Hotplug back the hostdev interface/device, check the network
        connectivity again
        """
        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev)

        test.log.info("TEST_STEP2: Start the VM with iommu enabled")
        vm.start()
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(timeout=240)

        test.log.info("TEST_STEP3: Check network accessibility")
        br_name = None
        if test_scenario == 'failover':
            br_name = br_dict['source'].get('bridge')
        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)
        check_points.check_vm_iface_num(vm_session, expr_iface_no)

        test.log.info("TEST_STEP4: Hotunplug a hostdev interface/device")
        iface_cur = libvirt.get_vm_device(
            vm_xml.VMXML.new_from_dumpxml(vm_name), device_type, index=-1)[0]
        virsh.detach_device(vm_name, iface_cur.xml, wait_for_event=True,
                            debug=True, ignore_status=False)
        check_points.check_vm_iface_num(vm_session, expr_iface_no-1)

        test.log.info("TEST_STEP5: Hotplug back the hostdev interface/device, "
                      "check the network connectivity.")
        virsh.attach_device(vm_name, iface_cur.xml, debug=True,
                            ignore_status=False)

        check_points.check_vm_iface_num(vm_session, expr_iface_no,
                                        timeout=40, first=15)
        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)

    dev_type = params.get("dev_type", "")
    test_scenario = params.get("test_scenario", "")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    if dev_type == "hostdev_device":
        dev_name = sriov_test_obj.pf_dev_name
    else:
        dev_name = sriov_test_obj.vf_dev_name
    iface_dict = sriov_test_obj.parse_iface_dict()
    network_dict = sriov_test_obj.parse_network_dict()

    device_type = "hostdev" if dev_type == "hostdev_device" else "interface"
    managed_disabled = iface_dict.get('managed') != "yes" or \
        (network_dict and network_dict.get('managed') != "yes")
    test_dict = sriov_test_obj.parse_iommu_test_params()
    test_dict.update({"network_dict": network_dict,
                      "managed_disabled": managed_disabled,
                      "dev_name": dev_name})
    br_dict = test_dict.get('br_dict', {'source': {'bridge': 'br0'}})
    expr_iface_no = int(params.get("expr_iface_no", '1'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        sriov_test_obj.setup_iommu_test(**test_dict)
        run_test()

    finally:
        sriov_test_obj.teardown_iommu_test(orig_config_xml, **test_dict)
