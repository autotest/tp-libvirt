from virttest import virsh

from virttest.libvirt_xml.nodedev_xml import NodedevXML
from virttest.utils_kernel_module import KernelModuleHandler
from virttest.utils_libvirt import libvirt_vfio

from provider.sriov import sriov_base


def run(test, params, env):
    """Detach node device from host without device driver."""
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    dev_name = sriov_test_obj.pf_dev_name
    dev_pci = sriov_test_obj.pf_pci
    module_name = sriov_test_obj.pf_info.get('driver')

    try:
        test.log.info("TEST_STEP: Remove module %s.", module_name)
        KernelModuleHandler(module_name).unload_module()
        dev_driver = NodedevXML.new_from_dumpxml(dev_name).get('driver_name')
        if dev_driver:
            test.fail("There should be no driver, but got '%s'!" % dev_driver)

        test.log.info("TEST_STEP: Detach the node device.")
        virsh.nodedev_detach(dev_name, debug=True, ignore_status=False)
        libvirt_vfio.check_vfio_pci(dev_pci)
        dev_driver = NodedevXML.new_from_dumpxml(dev_name).get('driver_name')
        if dev_driver == "vfio-pci":
            test.fail("Got incorrect device driver '%s'!" % dev_driver)

        test.log.info("TEST_STEP: Reattach the node device.")
        virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
        dev_driver = NodedevXML.new_from_dumpxml(dev_name).get('driver_name')
        if dev_driver:
            test.fail("There should be no driver, but got '%s'!" % dev_driver)
    finally:
        test.log.info("TEST_TEARDOWN: Reload module %s.", module_name)
        KernelModuleHandler(module_name).reload_module(True)
        test.log.info("TEST_TEARDOWN: Reattach the node device.")
        virsh.nodedev_reattach(dev_name, debug=True)
