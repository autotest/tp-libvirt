from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vfio
from virttest.utils_libvirt import libvirt_vmxml

from provider.sriov import check_points
from provider.sriov import sriov_base


def run(test, params, env):
    """
    Confirm a device occupied can not be used by others
    """
    def get_vm_session(vm):
        """
        Get VM's session

        :param vm: VM object
        :return: The session of VM
        """
        vm.start()
        return vm.wait_for_serial_login(timeout=240, recreate_serial_console=True)

    def run_test():
        """
        Try to start other VM with the same VF device
        """
        test.log.info("TEST_STEP1: Start the vm.")
        vm_session = get_vm_session(vm)
        libvirt_vfio.check_vfio_pci(sriov_test_obj.vf_pci, True)
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)

        test.log.info("TEST_STEP2: Attach and detach an interface.")
        virsh.attach_device(vm.name, iface_dev.xml,
                            debug=True, ignore_status=False)

        virsh.detach_device(vm.name, iface_dev.xml,
                            debug=True, ignore_status=False,
                            wait_for_event=True)

        if test_scenario == "to_2nd_vm":
            test_vm = vm_list[1]
            libvirt_vmxml.remove_vm_devices_by_type(test_vm, 'interface')
            vm_session = get_vm_session(test_vm)
        else:
            test_vm = vm

        test.log.info("TEST_STEP3: Attach the same VF device to %s.", test_vm.name)
        virsh.attach_device(test_vm.name, iface_dev.xml,
                            debug=True, ignore_status=False)

        test.log.info("TEST_STEP4: Check device driver and network connection.")
        libvirt_vfio.check_vfio_pci(sriov_test_obj.vf_pci)
        check_points.check_vm_network_accessed(vm_session)

        test.log.info("TEST_STEP5: Detach the interface and check the driver.")
        virsh.detach_device(test_vm.name, iface_dev.xml,
                            debug=True, ignore_status=False,
                            wait_for_event=True)

        libvirt_vfio.check_vfio_pci(sriov_test_obj.vf_pci, True)

    dev_type = params.get("dev_type", "hostdev_interface")
    test_scenario = params.get("test_scenario", "")
    vms = params.get('vms').split()
    vm_list = [env.get_vm(v_name) for v_name in vms]
    vm = vm_list[0]
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()

    if len(vm_list) >= 2:
        vm2_xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_list[1].name)

    try:
        sriov_test_obj.setup_default()
        run_test()

    finally:
        if "vm2_xml_backup" in locals():
            vm2_xml_backup.sync()
        sriov_test_obj.teardown_default()
