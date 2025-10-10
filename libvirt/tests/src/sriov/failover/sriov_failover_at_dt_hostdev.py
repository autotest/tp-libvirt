from provider.sriov import check_points
from provider.sriov import sriov_base

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Attach/detach the hostdev interface with failover setting
    """
    def run_test():
        """
        Check the network connectivity after attaching/detaching the hostdev
        interface/device.

        1. Start vm with failover setting interfaces
        2. Check VM's interface quantity and network connectivity
        3. Hot-unplug the hostdev interface/device by detach-device
        4. After hot-unplug the hostdev interface, ensure the bridge interface
            will be active, so check on vm for 1) interface quantity; 2)network
            connectivity
        5. Hot plug the hostdev interface/device back by attach-device
        6. Check on vm for 1) interface quantity; 2)network connectivity
        """

        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev)

        test.log.info("TEST_STEP2: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240, recreate_serial_console=True)

        test.log.info("TEST_STEP3: Check interface quantity and network"
                      "accessibility.")
        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)
        check_points.check_vm_iface_num(vm_session, expr_iface_no)

        test.log.info("TEST_STEP4: Hot-unplug a hostdev interface/device")
        iface_cur = libvirt.get_vm_device(
            vm_xml.VMXML.new_from_dumpxml(vm_name), device_type, index=-1)[0]
        virsh.detach_device(vm_name, iface_cur.xml, wait_for_event=True,
                            debug=True, ignore_status=False)
        check_points.check_vm_iface_num(vm_session, expr_iface_no-1)

        test.log.info("TEST_STEP5: Hotplug back the hostdev interface/device, "
                      "check interface quantity and the network connectivity.")
        virsh.attach_device(vm_name, iface_cur.xml, debug=True,
                            ignore_status=False)

        check_points.check_vm_iface_num(vm_session, expr_iface_no,
                                        timeout=40, first=15)
        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)

    dev_type = params.get("dev_type", "")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()
    device_type = "hostdev" if dev_type == "hostdev_device" else "interface"
    test_dict = sriov_test_obj.parse_iommu_test_params()

    br_dict = test_dict.get('br_dict', {'source': {'bridge': 'br0'}})
    br_name = br_dict['source'].get('bridge')

    expr_iface_no = int(params.get("expr_iface_no", '3'))

    try:
        sriov_test_obj.setup_failover_test(**test_dict)
        run_test()

    finally:
        sriov_test_obj.teardown_failover_test(**test_dict)
