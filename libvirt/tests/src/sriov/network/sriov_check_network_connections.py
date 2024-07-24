from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml

from provider.sriov import sriov_base


def run(test, params, env):
    """
    Check the number of connections on hostdev network
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    network_dict = sriov_test_obj.parse_network_dict()
    net_name = network_dict.get("name")
    iface_dict = eval(params.get("iface_dict", "{}"))
    iface_nums = int(params.get("iface_nums", "2"))

    try:
        sriov_test_obj.setup_default(network_dict=network_dict)
        for idx in range(iface_nums):
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_inactive_dumpxml(vm_name),
                'interface', iface_dict, idx)
        test.log.debug(f"vm xml: {vm_xml.VMXML.new_from_dumpxml(vm_name)}")

        test.log.info("TEST_STEP: Start a VM with hostdev interfaces.")
        vm.start()
        vm.wait_for_serial_login().close()

        test.log.info(f"TEST_STEP: Check if there are {iface_nums} network connections.")
        libvirt_network.check_network_connection(net_name, iface_nums)

        test.log.info(f"TEST_STEP: Destroy and start network and vm.")
        virsh.net_destroy(net_name, debug=True, ignore_status=False)
        virsh.net_start(net_name, debug=True, ignore_status=False)
        vm.destroy()
        vm.start()

        test.log.info(f"TEST_STEP: Check if there are {iface_nums} network connections.")
        libvirt_network.check_network_connection(net_name, iface_nums)
    finally:
        sriov_test_obj.teardown_default(network_dict=network_dict)
