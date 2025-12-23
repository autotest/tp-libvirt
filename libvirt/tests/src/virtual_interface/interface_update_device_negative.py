from provider.interface import interface_base
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Update an interface by update-device(negative)
    """
    libvirt_version.is_libvirt_feature_supported(params)

    pre_iface_dict = eval(params.get("pre_iface_dict", "{}"))
    start_vm = "yes" == params.get("start_vm", "no")

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    try:
        if pre_iface_dict:
            libvirt_vmxml.modify_vm_device(vmxml, 'interface', pre_iface_dict)
        if start_vm and not vm.is_alive():
            vm.start()
            session = vm.wait_for_serial_login(timeout=int(params.get("login_timeout", 600)), recreate_serial_console=True)
            session.close()
        test.log.info("TEST_STEP: Update interface device.")
        interface_base.update_iface_device(vm, params)
    finally:
        backup_vmxml.sync()
