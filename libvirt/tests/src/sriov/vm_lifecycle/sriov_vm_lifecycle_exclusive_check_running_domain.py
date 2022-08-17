from provider.sriov import check_points
from provider.sriov import sriov_base

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Confirm a device occupied can not be used by others
    """
    def coldplug_iface():
        """
        Cold plug a hostdev interface to VM
        """
        if test_scenario in ['start_2nd_vm', 'hotplug']:
            if len(vm_list) != 2:
                test.cancel('More or less than 2 vms is currently unsupported')
        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        virsh.attach_device(vm.name, iface_dev.xml, flagstr="--config",
                            debug=True, ignore_status=False)

    def get_vm_session(vm):
        """
        Get VM's session

        :param vm: VM object
        :return: The session of VM
        """
        vm.start()
        vm.cleanup_serial_console()
        vm.create_serial_console()
        return vm.wait_for_serial_login(timeout=240)

    def run_start_2nd_vm():
        """
        Try to start other VM with the same VF device
        """
        sriov_test_obj2 = sriov_base.SRIOVTest(vm, test, test_dict)
        iface_dict2 = sriov_test_obj2.parse_iface_dict()

        coldplug_iface()
        vm_session = get_vm_session(vm)

        vm2 = vm_list[1]
        test.log.info("TEST_STEP2: Attach the same VF device to VM2.")
        iface_dev2 = sriov_test_obj.create_iface_dev(dev_type2, iface_dict2)
        virsh.attach_device(vm2.name, iface_dev2.xml,
                            flagstr="--config", debug=True, ignore_status=False)

        test.log.info("TEST_STEP2: Start VM2")
        result = virsh.start(vm2.name, debug=True)
        libvirt.check_exit_status(result, True)
        if err_msg:
            libvirt.check_result(result, err_msg)
        check_points.check_vm_network_accessed(vm_session)

    def run_assigned_VF_to_host():
        """
        Try to attach the assigned VF to host
        """
        coldplug_iface()
        vm_session = get_vm_session(vm)

        test.log.info("TEST_STEP2: Detach the VF device.")
        result = virsh.nodedev_detach(sriov_test_obj.vf_dev_name, debug=True)
        libvirt.check_exit_status(result, True)
        if err_msg:
            libvirt.check_result(result, err_msg)

        test.log.info("TEST_STEP2: Reattach the VF device.")
        result = virsh.nodedev_reattach(sriov_test_obj.vf_dev_name, debug=True)
        libvirt.check_exit_status(result, True)
        if err_msg:
            libvirt.check_result(result, err_msg)

        check_points.check_vm_network_accessed(vm_session)

    def run_hotplug():
        """
        Try to hotplug the VF to another running VM or itself
        """
        def check_hostdev_attach(vm, iface_dev):
            """
            Attach a hostdev iface/device and check the result

            :param vm: VM object
            :param iface_dev: Interface device object
            """
            result = virsh.attach_device(vm.name, iface_dev.xml,
                                         debug=True)
            libvirt.check_exit_status(result, True)
            if err_msg:
                libvirt.check_result(result, err_msg)

        sriov_test_obj2 = sriov_base.SRIOVTest(vm, test, test_dict)
        iface_dict2 = sriov_test_obj2.parse_iface_dict()
        coldplug_iface()

        vm_session = get_vm_session(vm)

        vm2 = vm_list[1]
        vm2.start()
        vm2.wait_for_serial_login(timeout=240).close()

        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        iface_dev2 = sriov_test_obj.create_iface_dev(dev_type2, iface_dict2)

        test.log.info("TEST_STEP2: Attach a hostdev interface/device to another "
                      "running VM and itself")
        for vm_obj in [vm, vm2]:
            for iface_dev_obj in [iface_dev, iface_dev2]:
                check_hostdev_attach(vm_obj, iface_dev_obj)

        check_points.check_vm_network_accessed(vm_session)

    dev_type = params.get("dev_type", "")
    dev_type2 = params.get("dev_type2", "")

    err_msg = params.get("err_msg")
    test_scenario = params.get("test_scenario", "")
    run_test = eval("run_%s" % test_scenario)
    vms = params.get('vms').split()
    vm_list = [env.get_vm(v_name) for v_name in vms]
    vm = vm_list[0]
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()

    test_dict = {'iface_dict': params.get("iface_dict2"),
                 'hostdev_dict': params.get("hostdev_dict2")}

    if len(vm_list) >= 2:
        vm2_xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_list[1].name)

    try:
        sriov_test_obj.setup_default()
        run_test()

    finally:
        if "vm2_xml_backup" in locals():
            vm2_xml_backup.sync()
        sriov_test_obj.teardown_default()
        if test_scenario == "assigned_VF_to_host":
            virsh.nodedev_reattach(sriov_test_obj.vf_dev_name, debug=True)
