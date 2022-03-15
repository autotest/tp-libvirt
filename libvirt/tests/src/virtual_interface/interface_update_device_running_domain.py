from virttest.libvirt_xml import vm_xml

from provider.interface import interface_base
from provider.interface import check_points


def run(test, params, env):
    """
    Update an interface by update-device with different options on a running
    domain.
    """
    # Variable assignment
    iface_dict = eval(params.get('iface_dict', '{}'))
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    try:
        # Execute test
        interface_base.update_iface_device(vm, params)
        if params.get('expr_active_xml_changes', 'no') == "yes":
            vmxml_active = vm_xml.VMXML.new_from_dumpxml(vm_name)
            check_points.comp_interface_xml(vmxml_active, iface_dict)
        if params.get('expr_inactive_xml_changes', 'no') == "yes":
            vmxml_active = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            check_points.comp_interface_xml(vmxml_active, iface_dict)
    finally:
        backup_vmxml.sync()
