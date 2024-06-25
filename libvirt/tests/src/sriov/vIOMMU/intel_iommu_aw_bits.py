from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.viommu import viommu_base


def run(test, params, env):
    """
    Start vm with different iommu address width values.
    """
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    aw_bits_value = params.get("aw_bits_value")
    err_msg = params.get("err_msg")
    status_error = "yes" == params.get("status_error", "no")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    test_obj = viommu_base.VIOMMUTest(vm, test, params)
    try:
        test_obj.setup_iommu_test(iommu_dict=iommu_dict)

        test.log.info("TEST_STEP: Start the VM.")
        result = virsh.start(vm.name, debug=True)
        libvirt.check_exit_status(result, status_error)
        if status_error:
            if err_msg:
                libvirt.check_result(result, err_msg)
            return
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm_session = vm.wait_for_serial_login(
            timeout=int(params.get('login_timeout')))
        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))

        test.log.info("TEST_STEP: Check dmesg message.")
        vm_session.cmd("dmesg | grep -i 'Host address width %s'" % aw_bits_value)
    finally:
        test_obj.teardown_iommu_test()
