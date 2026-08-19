import os

from provider.sriov import check_points
from provider.sriov import sriov_base

from virttest import data_dir
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Lifecycle test for vm with failover setting
    """
    def run_test():
        """
        Cover the lifecycle related tests including:

        1. reboot vm
        2. suspend -> resume
        3. save -> restore
        4. managedsave
        """
        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev)

        test.log.info("TEST_STEP2: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240, recreate_serial_console=True)

        test.log.info("TEST_STEP3: Check network accessibility")
        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)
        check_points.check_vm_iface_num(vm_session, expr_iface_no)

        test.log.info("TEST_STEP4: Reboot the vm")
        virsh.reboot(vm.name, debug=True, ignore_status=False)
        vm_session = vm.wait_for_serial_login(timeout=240)

        test.log.info("TEST_STEP3: Check network accessibility")
        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)
        check_points.check_vm_iface_num(vm_session, expr_iface_no)

        test.log.info("TEST_STEP4: Suspend and resume VM.")
        virsh.suspend(vm.name, debug=True, ignore_status=False)
        virsh.resume(vm.name, debug=True, ignore_status=False)

        test.log.info("TEST_STEP5: Check network accessibility")
        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)
        check_points.check_vm_iface_num(vm_session, expr_iface_no)

        test.log.info("TEST_STEP6: Save and restore VM.")
        save_file = os.path.join(data_dir.get_tmp_dir(), "save_file")
        virsh.save(vm_name, save_file, debug=True, ignore_status=False, timeout=10)
        if not libvirt.check_vm_state(vm_name, "shut off"):
            test.fail("The guest should be down after executing 'virsh save'.")
        virsh.restore(save_file, debug=True, ignore_status=False)
        if not libvirt.check_vm_state(vm_name, "running"):
            test.fail("The guest should be running after executing 'virsh restore'.")
        vm_session = vm.wait_for_serial_login(recreate_serial_console=True)

        test.log.info("TEST_STEP7: Check network accessibility")
        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)
        check_points.check_vm_iface_num(vm_session, expr_iface_no)

        test.log.info("TEST_STEP8: Managedsave the VM.")
        virsh.managedsave(vm_name, debug=True, ignore_status=False, timeout=10)
        vm.start()
        vm_session = vm.wait_for_serial_login(recreate_serial_console=True)

        test.log.info("TEST_STEP9: Check network accessibility")
        check_points.check_vm_network_accessed(vm_session,
                                               tcpdump_iface=br_name,
                                               tcpdump_status_error=True)
        check_points.check_vm_iface_num(vm_session, expr_iface_no)

    dev_type = params.get("dev_type", "")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()
    test_dict = sriov_test_obj.parse_iommu_test_params()

    br_dict = test_dict.get('br_dict', {'source': {'bridge': 'br0'}})
    br_name = br_dict['source'].get('bridge')

    expr_iface_no = int(params.get("expr_iface_no", '3'))

    try:
        sriov_test_obj.setup_failover_test(**test_dict)
        run_test()

    finally:
        sriov_test_obj.teardown_failover_test(**test_dict)
