from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Hotplug or coldplug iommu device to guest
    """
    libvirt_version.is_libvirt_feature_supported(params)
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    attach_option = params.get("attach_option", "")
    err_msg = params.get("err_msg")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()
    try:
        if attach_option and vm.is_alive():
            vm.destroy()
        elif not attach_option and not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()
        if libvirt_version.version_compare(10, 5, 0) and attach_option:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
            features = vmxml.features
            if not features.has_feature('ioapic') and iommu_dict.get('model') == "intel":
                features.add_feature('ioapic', 'driver', 'qemu')
                vmxml.features = features
                vmxml.sync()
            err_msg = ''

        iommu_dev = libvirt_vmxml.create_vm_device_by_type('iommu', iommu_dict)
        test.log.debug(f"iommu device: {iommu_dev}")
        res = virsh.attach_device(vm.name, iommu_dev.xml, debug=True,
                                  flagstr=attach_option)
        libvirt.check_result(res, err_msg)
    finally:
        backup_vmxml.sync()
