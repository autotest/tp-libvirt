from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.viommu import viommu_base
from provider.virtual_network import network_base


def run(test, params, env):
    """
    Run netperf testing between host and vm with iommu device
    """
    cleanup_ifaces = "yes" == params.get("cleanup_ifaces", "yes")
    iommu_dict = eval(params.get('iommu_dict', '{}'))

    vms = params.get('vms').split()
    vm_objs = [env.get_vm(vm_i) for vm_i in vms]

    test_objs = [viommu_base.VIOMMUTest(vm, test, params) for vm in vm_objs]

    try:
        test.log.info("TEST_SETUP: Update VM XML.")
        for test_obj in test_objs:
            test_obj.setup_iommu_test(iommu_dict=iommu_dict,
                                      cleanup_ifaces=cleanup_ifaces)

        iface_dict = test_objs[0].parse_iface_dict()
        if cleanup_ifaces:
            vmxml_lists = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))
            [libvirt_vmxml.modify_vm_device(vmxml_i, 'interface', iface_dict) for vmxml_i in vmxml_lists]

        test.log.info('TEST_STEP: Start the VM(s)')
        [vm_inst.start() for vm_inst in vm_objs]
        [vm_inst.wait_for_login() for vm_inst in vm_objs]

        test.log.info("TEST_STEP: Run netperf testing between host(vm) and vm.")
        network_base.exec_netperf_test(params, env)
    finally:
        for test_obj in test_objs:
            test_obj.teardown_iommu_test()
