from provider.sriov import sriov_base

from virttest import virsh

from virttest.libvirt_xml import network_xml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'debug': True}


def run(test, params, env):
    """
    Check net info
    """
    def run_test():
        """
        Libvirt forbid to define or start 'hostdev' network which contains PF
        pci address
        """
        test.log.info("TEST_STEP1: Try to define network with PF pci address.")
        result = virsh.net_define(net_dev.xml, **VIRSH_ARGS)
        libvirt.check_result(result, err_msg)
        test.log.info("TEST_STEP2: Try to create network with PF pci address.")
        result = virsh.net_create(net_dev.xml, **VIRSH_ARGS)
        libvirt.check_result(result, err_msg)

    err_msg = params.get("err_msg")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    network_dict = sriov_test_obj.parse_network_dict()
    network_name = network_dict['name']
    net_dev = network_xml.NetworkXML()
    net_dev.setup_attrs(**network_dict)
    try:
        run_test()

    finally:
        virsh.net_destroy(network_name, debug=True)
        virsh.net_undefine(network_name, debug=True)
