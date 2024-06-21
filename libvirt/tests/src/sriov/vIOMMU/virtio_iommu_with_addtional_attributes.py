from provider.viommu import viommu_base

from virttest.libvirt_xml import xcepts
from virttest.utils_libvirt import libvirt_virtio


def run(test, params, env):
    """
    virtio iommu do not support any additional attributes
    """
    err_msg = params.get("err_msg")
    iommu_dict = eval(params.get('iommu_dict', '{}'))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    test_obj = viommu_base.VIOMMUTest(vm, test, params)

    try:
        libvirt_virtio.add_iommu_dev(vm, iommu_dict)
    except xcepts.LibvirtXMLError as details:
        if err_msg:
            test.log.debug("Check '%s' in %s.", err_msg, details)
            if not str(details).count(err_msg):
                test.fail("Incorrect error message, it should be '{}', but "
                          "got '{}'.".format(err_msg, details))
    else:
        test.fail("Defining the VM should fail, but run successfully!")
    finally:
        test_obj.teardown_iommu_test()
