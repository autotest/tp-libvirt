from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    libvirt will automatically add "<ioapic driver='qemu'/>"
    if <driver intremap='on'/> is set for the intel iommu device
    """
    libvirt_version.is_libvirt_feature_supported(params)
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    auto_add_ioapic = params.get("auto_add_ioapic", "no") == "yes"
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
        vmxml.sync()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        features = vmxml.features
        tmp_msg = "" if auto_add_ioapic else "not "
        msg = (f"{feature_name} should {tmp_msg}be "
               f"added automatically! Actual feature list: {features}")
        if auto_add_ioapic == features.has_feature(feature_name):
            test.log.debug(msg)
        else:
            test.fail(msg)
        virsh.start(vm.name, debug=True, ignore_status=False)
    finally:
        backup_vmxml.sync()
