from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Check for the error message when using an Intel iommu device
    but without <ioapic driver='qemu'/> defined in the VM
    """
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    err_msg = params.get("err_msg", "I/O APIC")
    feature_name = params.get("feature_name", "ioapic")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()
    try:
        test.log.info(f"TEST_STEP: Remove {feature_name} feature if needed.")
        features = vmxml.features
        if features.has_feature(feature_name):
            features.remove_feature(feature_name)
            vmxml.features = features

        test.log.info("TEST_STEP: Define VM with intel iommu device.")
        iommu_dev = libvirt_vmxml.create_vm_device_by_type('iommu', iommu_dict)
        vmxml.add_device(iommu_dev)
        vmxml.xmltreefile.write()
        try:
            vmxml.sync()
        except xcepts.LibvirtXMLError as details:
            test.log.debug("Check '%s' in %s.", err_msg, details)
            if not str(details).count(err_msg):
                test.fail("Incorrect error message, it should be '{}', but "
                          "got '{}'.".format(err_msg, details))
        else:
            test.fail("Vm is expected to fail on defining with intel iommu "
                      "without ioapic feature, while it succeeds.")
    finally:
        backup_vmxml.sync()
