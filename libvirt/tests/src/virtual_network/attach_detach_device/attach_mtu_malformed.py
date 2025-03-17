import logging

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test attach interface with mtu configuration
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    rand_id = utils_misc.generate_random_string(3)
    net_name = 'net_' + rand_id
    source_net = params.get('source_net', net_name)
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True, json=True)
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    net_attrs = eval(params.get('net_attrs', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        vmxml.sync()
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs)
            LOG.debug(f'Network xml:\n'
                      f'{virsh.net_dumpxml(net_attrs["name"]).stdout_text}')

        vm.start()
        vm.wait_for_serial_login().close()

        iface = libvirt_vmxml.create_vm_device_by_type(
            'interface', iface_attrs)
        LOG.debug(f'Interface to attach:\n{iface}')

        at_result = virsh.attach_device(vm_name, iface.xml)
        libvirt.check_exit_status(at_result, status_error)
        if err_msg:
            libvirt.check_result(at_result, err_msg)

    finally:
        bkxml.sync()
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
