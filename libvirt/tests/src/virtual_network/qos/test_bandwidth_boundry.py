import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test bandwidth boundary of an interface via domiftune

    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    bw_type = params.get('bw_type', '')
    bw_args = params.get('bw_args', '')
    status_error = 'yes' == params.get("status_error", 'no')
    err_msg = params.get('err_msg')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        mac = iface.mac_address
        domiftune_args = {bw_type: bw_args}
        virsh_result = virsh.domiftune(
            vm_name, mac, **domiftune_args, debug=True)
        libvirt.check_exit_status(virsh_result, status_error)
        if status_error:
            libvirt.check_result(virsh_result, err_msg)
            return

        out = virsh.domiftune(vm_name, mac, **VIRSH_ARGS).stdout_text

        bw_out = libvirt_misc.convert_to_dict(
            out, '(\S+)\s*:\s*(\d+)')
        bw_values = bw_out[f'{bw_type}.average'], \
            bw_out[f'{bw_type}.peak'], bw_out[f'{bw_type}.burst']
        if ','.join(bw_values) != bw_args:
            test.fail(f'Bandwidth from domiftune is {",".join(bw_values)}, '
                      f'but it should be {bw_args}')

        iface = network_base.get_iface_xml_inst(vm_name, 'after domiftune')
        iface_attrs = iface.fetch_attrs()
        bw_attrs = iface_attrs['bandwidth'][bw_type]
        bw_xml_values = ','.join([bw_attrs['average'], bw_attrs['peak'],
                                  bw_attrs['burst']])
        if bw_xml_values != bw_args:
            test.fail(f'Bandwidth from interface xml is {bw_xml_values}, '
                      f'but it should be {bw_args}')
    finally:
        bkxml.sync()
