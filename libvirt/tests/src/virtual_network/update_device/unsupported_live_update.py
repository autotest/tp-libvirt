import logging

from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test update-device command to update coalesce of interface
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    set_boot = 'yes' == params.get('set_boot', 'no')

    mac = utils_net.generate_mac_address_simple()
    base_iface_attrs = eval(params.get('base_iface_attrs', '{}'))
    extra_attrs = eval(params.get('extra_attrs', '{}'))
    iface_attrs = {**base_iface_attrs, **extra_attrs}

    update_attrs = eval(params.get('update_attrs', '{}'))
    del_attr = params.get('del_attr')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if set_boot:
            osxml = vmxml.os
            osxml.del_boots()
            vmxml.os = osxml
            libvirt_vmxml.modify_vm_device(vmxml, 'disk', {'boot': '1'})

        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()
        vm.wait_for_serial_login().close()

        iface = network_base.get_iface_xml_inst(vm_name, 'on VM')
        iface_original = iface.copy()
        if del_attr:
            if del_attr == 'boot':
                iface.xmltreefile.remove_by_xpath('/boot')
            else:
                eval(f'iface.del_{del_attr}')()
        else:
            iface.setup_attrs(**update_attrs)
        LOG.debug(f'Interface xml to update with:\n{iface}')

        up_result = virsh.update_device(vm_name, iface.xml, debug=True)
        libvirt.check_exit_status(up_result, status_error)
        if err_msg:
            libvirt.check_result(up_result, err_msg)

        iface_update = network_base.get_iface_xml_inst(vm_name,
                                                       'after update-device')
        if iface_update != iface_original:
            test.fail(f'Interface xml changed after unsuccessful update.\n'
                      f'Should be:\n{iface_original}\n'
                      f'Actually is:\n{iface_update}')
    finally:
        bkxml.sync()
