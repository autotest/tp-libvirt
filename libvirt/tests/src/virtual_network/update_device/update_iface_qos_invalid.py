import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test live update interface bandwidth setting by update-device or domiftune
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    update_attrs = eval(params.get('update_attrs', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()
        vm.wait_for_serial_login().close()

        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        LOG.debug(f'Update iface with attrs: {update_attrs}')
        iface.setup_attrs(**update_attrs)

        LOG.debug(f'Update iface with xml:\n{iface}')
        up_result = virsh.update_device(vm_name, iface.xml, debug=True)
        libvirt.check_exit_status(up_result, status_error)
        if err_msg:
            libvirt.check_result(up_result, err_msg)
    finally:
        bkxml.sync()
