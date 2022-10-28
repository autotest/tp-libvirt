from provider.sriov import sriov_base

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
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

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
        sriov_test_obj.teardown_default()
