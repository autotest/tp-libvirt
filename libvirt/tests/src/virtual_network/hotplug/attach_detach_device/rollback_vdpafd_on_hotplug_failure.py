from provider.interface import interface_base
from provider.interface import vdpa_base

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Check opened fd on vdpa device is closed when device attaching failed
    """

    libvirt_version.is_libvirt_feature_supported(params)

    # Variable assignment
    test_target = params.get('test_target', '')
    dev_type = params.get('dev_type', 'vdpa')
    iface_dict2 = eval(params.get("iface_dict2", "{}"))
    iface2_dev = iface_dict2["source"]["dev"]

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    test_obj = None
    try:
        test_obj, _ = vdpa_base.setup_vdpa(vm, params)
        test.log.info("TEST_STEP: Start a vm, attach a vdpa interface with "
                      "acpi index=1.")
        vm.start()
        interface_base.attach_iface_device(vm_name, dev_type, params)

        test.log.info("TEST_STEP: Hotplug another vdpa interface with "
                      "acpi index=1.")
        iface2 = interface_base.create_iface("vdpa", iface_dict2)
        res = virsh.attach_device(vm.name, iface2.xml, debug=True)
        libvirt.check_exit_status(res, True)
        res = process.run(f"lsof {iface2_dev}", verbose=True, ignore_status=True)
        libvirt.check_exit_status(res, True)
        if res.stdout_text:
            test.fail(f"{iface2_dev} may be used by qemu - {res.stdout_text}")

        test.log.info("TEST_STEP: Hotplug another vdpa interface with "
                      "acpi index=2.")
        iface2.setup_attrs(**{'acpi': {'index': '2'}})
        virsh.attach_device(vm.name, iface2.xml, debug=True, ignore_status=False)
    finally:
        backup_vmxml.sync()
        vdpa_base.cleanup_vdpa(test_target, test_obj)
