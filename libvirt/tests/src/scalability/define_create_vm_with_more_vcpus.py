from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    libvirt should automatically add iommu and ioapic setting
    when define/create guest with more than 255 vcpus
    """
    libvirt_version.is_libvirt_feature_supported(params)
    iommu_dict = eval(params.get("iommu_dict", "{}"))
    exp_iommu_dict = eval(params.get("exp_iommu_dict", "{}"))
    define_vm = params.get("define_vm", "no") == "yes"
    with_ioapic = params.get("with_ioapic", "no") == "yes"
    feature_name = params.get("feature_name", "ioapic")
    feature_attr = eval(params.get("feature_attr", "[]"))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()
    try:
        libvirt_vmxml.remove_vm_devices_by_type(vm, "iommu")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_attrs = eval(params.get("vm_attrs", "{}"))
        vmxml.setup_attrs(**vm_attrs)

        test.log.info(f"TEST_STEP: Update {feature_name} feature.")
        features = vmxml.features
        test.log.debug(f"orignal features: {features}")
        if features.has_feature(feature_name):
            if not with_ioapic:
                test.log.debug(f"removing {feature_name}...")
                features.remove_feature(feature_name)
        else:
            if with_ioapic:
                test.log.debug(f"Adding {feature_name}...")
                features.add_feature(feature_name, *feature_attr)
        vmxml.features = features

        if iommu_dict:
            test.log.info("TEST_STEP: Define VM with intel iommu device.")
            iommu_dev = libvirt_vmxml.create_vm_device_by_type(
                "iommu", iommu_dict)
            vmxml.add_device(iommu_dev)
        vmxml.xmltreefile.write()
        test.log.debug(f"vm xml will be updated as below: \n{vmxml}")
        if define_vm:
            vmxml.sync()
        else:
            virsh.destroy(vm_name, debug=True)
            virsh.undefine(vm_name, options='--nvram', debug=True, ignore_status=False)
            virsh.create(vmxml.xml, debug=True, ignore_status=False)

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        features = vmxml.features
        test.log.debug(f"features after updating: {features}")
        if not features.has_feature(feature_name):
            test.fail("Failed to get feature - %s." % feature_name)

        if exp_iommu_dict:
            actual_iommu = vm_xml.VMXML.new_from_dumpxml(vm.name)\
                .devices.by_device_tag("iommu")[0].fetch_attrs()

            test.log.debug(f"actual iommu device: {actual_iommu}\n"
                           f"expected iommu device: {exp_iommu_dict}")
            if exp_iommu_dict != actual_iommu:
                test.log.warning("iommu device xml comparison failed. "
                                 "Adding alias and try again...")
                exp_iommu_dict.update({"alias": {"name": "iommu0"}})
                if exp_iommu_dict != actual_iommu:
                    test.fail("Incorrect iommu device!")
    finally:
        if not define_vm:
            vm.define(vmxml.xml)
        backup_vmxml.sync()
