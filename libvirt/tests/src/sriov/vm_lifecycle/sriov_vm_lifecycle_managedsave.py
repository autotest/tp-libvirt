from provider.sriov import sriov_base

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Managedsave vm with a hostdev interface/device
    """
    def run_test():
        """
        Managedsave vm with a hostdev interface/device is not supported

        1) Start the VM
        2) Managedsave the VM and check the error message
        """

        test.log.info("TEST_STEP1: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt.add_vm_device(vmxml, iface_dev)

        test.log.info("TEST_STEP2: Start the VM")
        vm.start()
        vm.wait_for_serial_login(timeout=240, recreate_serial_console=True).close()

        test.log.info("TEST_STEP3: Managedsave the vm")
        result = virsh.managedsave(vm.name, debug=True)
        libvirt.check_exit_status(result, status_error)
        if err_msg:
            libvirt.check_result(result, err_msg)

    dev_type = params.get("dev_type", "")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    status_error = "yes" == params.get("status_error", "yes")
    err_msg = params.get("err_msg")
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    iface_dict = sriov_test_obj.parse_iface_dict()

    try:
        sriov_test_obj.setup_default()
        run_test()

    finally:
        sriov_test_obj.teardown_default()
