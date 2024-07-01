import logging

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check the floor setting in bandwidth setting

    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')

    rand_id = utils_misc.generate_random_string(3)
    net_name = params.get('net_type') + '_' + rand_id
    net_attrs = eval(params.get('net_attrs', '{}'))
    iface_attrs = eval(params.get('iface_attrs', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs)
            LOG.debug(f'Network xml:\n'
                      f'{virsh.net_dumpxml(net_attrs["name"]).stdout_text}')

        vmxml.del_device('interface', by_tag=True)
        iface = libvirt_vmxml.create_vm_device_by_type(
            'interface', iface_attrs)
        libvirt.add_vm_device(vmxml, iface)
        LOG.debug(f'VMXMLof {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        start_result = virsh.start(vm_name, debug=True)
        libvirt.check_exit_status(start_result, status_error)
        if err_msg:
            libvirt.check_result(start_result, err_msg)
        if status_error:
            return

        iface = network_base.get_iface_xml_inst(vm_name, '1st on vm')
        mac = iface.mac_address
        source_br = iface.source['bridge']

        tap_device = libvirt.get_ifname_host(vm_name, mac)
        LOG.debug(f'tap device on host with mac {mac} is: {tap_device}.')

        bw_floor = iface_attrs['bandwidth']['inbound']['floor']
        iface_attrs['bandwidth']['inbound'].pop('floor')

        if not utils_net.check_class_rules(
                source_br, '1:3', {'floor': bw_floor}):
            test.fail('Class rule check (floor) failed')
        if not utils_net.check_class_rules(
                tap_device, '1:1', iface_attrs['bandwidth']['inbound']):
            test.fail('Class rule check failed')
        if not utils_net.check_filter_rules(
                tap_device, iface_attrs['bandwidth']['outbound']):
            test.fail('Filter rule check failed')

    finally:
        bkxml.sync()
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
