from virttest import utils_test

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.viommu import viommu_base


def run(test, params, env):
    """
    Transfer a file between host and vm with iommu device
    """
    cleanup_ifaces = "yes" == params.get("cleanup_ifaces", "yes")
    iommu_dict = eval(params.get('iommu_dict', '{}'))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    test_obj = viommu_base.VIOMMUTest(vm, test, params)

    try:
        test.log.info("TEST_SETUP: Update VM XML.")
        test_obj.setup_iommu_test(iommu_dict=iommu_dict,
                                  cleanup_ifaces=cleanup_ifaces)

        iface_dict = test_obj.parse_iface_dict()
        if cleanup_ifaces:
            libvirt_vmxml.modify_vm_device(
                    vm_xml.VMXML.new_from_dumpxml(vm.name),
                    "interface", iface_dict)

        test.log.info("TEST_STEP: Start the VM.")
        vm.start()
        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))

        test.log.info("TEST_STEP: Transfer a file between host and vm.")
        utils_test.run_file_transfer(test, params, env)
    finally:
        test_obj.teardown_iommu_test()
