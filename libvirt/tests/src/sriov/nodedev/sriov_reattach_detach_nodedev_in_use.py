from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.sriov import check_points
from provider.sriov import sriov_base


def run(test, params, env):
    """Test nodedev-detach or reattach when vf or PF is in use."""
    def run_test():
        """
        Run nodedev-detach or reattach when vf or PF is in use.

        1. start the vm with hostdev interface or device;
        2. on the host, run nodedev-detach for the PF or VF, should fail
        3. on the host, run nodedev-reattach for the PF or VF, should fail
        4. check on the VM for network function, should works well.
        """
        test.log.info("TEST_STEP: Start the VM.")
        dev_type = "interface" if 'interface' in params.get('dev_type') else "hostdev"
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_inactive_dumpxml(vm_name),
            dev_type, iface_dict)
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=int(params.get('login_timeout')))

        test.log.info("TEST_STEP: Detach nodedev %s.", dev_name)
        result = virsh.nodedev_detach(dev_name, debug=True)
        libvirt.check_result(result, err_msg)

        test.log.info("TEST_STEP: Reattach nodedev %s.", dev_name)
        result = virsh.nodedev_reattach(dev_name, debug=True)
        libvirt.check_result(result, err_msg)

        test.log.info("TEST_STEP: Check VM network accessibility.")
        check_points.check_vm_network_accessed(vm_session)

    dev_name = params.get("dev_name", "pf")
    err_msg = params.get("err_msg", "PCI device.* is in use by driver")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()
    if dev_name == "vf":
        dev_name = sriov_test_obj.vf_dev_name
    else:
        dev_name = sriov_test_obj.pf_dev_name

    try:
        sriov_test_obj.setup_default()
        run_test()

    finally:
        virsh.nodedev_reattach(dev_name, debug=True)
        sriov_test_obj.teardown_default()
