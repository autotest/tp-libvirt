from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.sriov import sriov_base


def run(test, params, env):
    """Live update of hostdev type interface is not supported."""
    dev_type = params.get("dev_type", "")
    err_msg = params.get("err_msg", "Failed to update device")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()

    try:
        sriov_test_obj.setup_default()
        test.log.info("TEST_STEP: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev)

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

        test.log.info("TEST_STEP: Update the interface.")
        iface_dev.update(eval(params.get("update_iface", "{}")))
        iface_new = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        result = virsh.update_device(vm_name, iface_new.xml, ignore_status=True, debug=True)
        libvirt.check_result(result, err_msg)
    finally:
        sriov_test_obj.teardown_default()
