from virttest.libvirt_xml import vm_xml

from provider.interface import interface_base
from provider.interface import check_points


def run(test, params, env):
    """
    Update an interface by update-device with different options on an offline
    domain.
    """
    # Variable assignment
    iface_dict = eval(params.get('iface_dict', '{}'))
    status_error = "yes" == params.get("status_error", "no")

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    try:
        # Execute test
        interface_base.update_iface_device(vm, params)
        vmxml_inactive = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        check_points.comp_interface_xml(
            vmxml_inactive, iface_dict, status_error)
    finally:
        backup_vmxml.sync()
