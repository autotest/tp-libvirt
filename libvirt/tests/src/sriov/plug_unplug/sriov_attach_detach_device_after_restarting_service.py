from provider.sriov import sriov_base

from virttest import utils_libvirtd
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Add one more hostdev interface/device after restarting libvirtd
    """
    def setup_test():
        """
        Setup test
        """
        sriov_test_obj.setup_default(network_dict=network_dict)
        test.log.info("TEST_SETUP: Start a vm with an interface, pointing to a "
                      "hostdev network.")
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_inactive_dumpxml(vm_name),
            'interface', pre_iface_dict)
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

    def run_test():
        """
        Add one more hostdev interface/device after restarting libvirtd to
        a guest with an existing interface(point to a hostdev network)
        """
        test.log.info("TEST_STEP1: Restart libvirtd")
        utils_libvirtd.Libvirtd("virtqemud").restart()

        test.log.info("TEST_STEP2: Attach a hostdev interface/device to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        virsh.attach_device(vm.name, iface_dev.xml, debug=True,
                            ignore_errors=False)

        test.log.info("TEST_STEP3: Detach the hostdev interface/device")
        virsh.detach_device(vm.name, iface_dev.xml, debug=True,
                            ignore_errors=False)

    dev_type = params.get("dev_type", "")
    pre_iface_dict = eval(params.get("pre_iface_dict", "{}"))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    iface_dict = sriov_test_obj.parse_iface_dict()
    network_dict = sriov_test_obj.parse_network_dict()
    try:
        setup_test()
        run_test()

    finally:
        sriov_test_obj.teardown_default(network_dict=network_dict)
