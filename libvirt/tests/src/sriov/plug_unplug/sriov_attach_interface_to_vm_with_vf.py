from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.sriov import sriov_base


def run(test, params, env):
    """
    Alternatively hotplug other interface and hostdev interface while the guest
    contains VF
    """
    def setup_test():
        """
        Setup test
        """
        sriov_test_obj.setup_default(network_dict=network_dict)
        test.log.info("TEST_SETUP: Start a vm with an interface, pointing to a "
                      "hostdev network.")
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_inactive_dumpxml(vm.name),
            'interface', pre_iface_dict)
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

    def run_test():
        """
        Attach virtual interface whose source is default network while the guest
        contains VF
        """
        test.log.info("TEST_STEP1: Attach-interface to the VM.")
        virsh.attach_interface(vm.name, virsh_opts, debug=True,
                               ignore_status=False)
        cur_ifaces = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("interface")
        if len(cur_ifaces) != 2:
            test.fail("VM's interface number is %d, it should be 2." % len(cur_ifaces))
        if cur_ifaces[1].get_source().get('network') != "default":
            test.fail("Incorrect network!")

    pre_iface_dict = eval(params.get("pre_iface_dict", "{}"))
    virsh_opts = params.get("virsh_opts", "network default --model virtio")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    network_dict = sriov_test_obj.parse_network_dict()
    try:
        setup_test()
        run_test()

    finally:
        sriov_test_obj.teardown_default(network_dict=network_dict)
