import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test attach interface with boot order
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    os_attrs = eval(params.get('os_attrs', '{}'))
    disk_attrs = eval(params.get('disk_attrs', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        vmxml.sync()

        if os_attrs:
            vmxml.setup_attrs(os=os_attrs)
        if disk_attrs:
            osxml = vmxml.os
            osxml.del_boots()
            vmxml.os = osxml
            vmxml.sync()
            libvirt_vmxml.modify_vm_device(vmxml, 'disk', disk_attrs)

        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()
        vm.wait_for_serial_login().close()

        iface = libvirt_vmxml.create_vm_device_by_type(
            'interface', iface_attrs)
        LOG.debug(f'Interface to attach:\n{iface}')

        def check_attach(option=''):
            at_result = virsh.attach_device(vm_name, iface.xml, flagstr=option,
                                            debug=True)
            libvirt.check_exit_status(at_result, status_error)
            if err_msg:
                libvirt.check_result(at_result, err_msg)

        check_attach()
        check_attach('--config')

    finally:
        bkxml.sync()
