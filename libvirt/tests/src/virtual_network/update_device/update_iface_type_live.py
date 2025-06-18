import logging
import re

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test update-device for bridge or direct type interface
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    rand_id = utils_misc.generate_random_string(3)
    create_linux_br = 'yes' == params.get('create_linux_br', 'no')
    create_ovs_br = 'yes' == params.get('create_ovs_br', 'no')
    check_link = 'yes' == params.get('check_link', 'yes')
    linux_br = 'br_' + rand_id
    operation = params.get('operation', 'attach_device')
    net_name = 'net_' + rand_id
    mac = utils_net.generate_mac_address_simple()
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    iface_type = params.get('iface_type')
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True, json=True)
    net_attrs = eval(params.get('net_attrs', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        vmxml.sync()
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        if create_linux_br:
            LOG.debug(f'Create linux bridge: {linux_br}')
            utils_net.create_linux_bridge_tmux(linux_br, host_iface)
        if create_ovs_br:
            LOG.debug(f'Create ovs bridge: {linux_br}')
            utils_net.create_ovs_bridge(linux_br)
        libvirt_network.create_or_del_network(net_attrs)
        LOG.debug(f'Network xml:\n'
                  f'{virsh.net_dumpxml(net_attrs["name"]).stdout_text}')

        iface = libvirt_vmxml.create_vm_device_by_type(
            'interface', iface_attrs)
        LOG.debug(f'Interface to attach/add:\n{iface.xmltreefile.__str__()}')

        if operation == 'default':
            vmxml.add_device(iface)
            vmxml.sync()

        vm.start()
        session = vm.wait_for_serial_login()

        if operation == 'attach_device':
            virsh.attach_device(vm_name, iface.xml,
                                flagstr='--persistent', **VIRSH_ARGS)
        elif operation == 'attach_interface':
            virsh.attach_interface(
                vm_name, f'--source {net_name} --model virtio --type network --persistent', **VIRSH_ARGS)

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_at = network_base.get_iface_xml_inst(vm_name, 'after attach')
        LOG.debug(f'Interface type after attached to vm: {iface_at.type_name}')
        if iface_at.type_name != iface_type:
            test.fail(f'Interface type should change to {iface_type}')

        LOG.debug('Update interface link state')
        link_state = 'down'
        iface.setup_attrs(**{'link_state': link_state})
        if params.get('with_iface_xml') == 'yes':
            xml_opt = params.get('xml_opt', '')
            iface = network_base.get_iface_xml_inst(
                vm_name, f'on vm ({xml_opt})', options=f'{xml_opt}')
        LOG.debug(f'Update iface with xml:\n{iface}')

        virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_update = network_base.get_iface_xml_inst(vm_name, 'after update')
        if not check_link:
            return

        LOG.debug(f'Link state of interface after update: '
                  f'{iface_update.link_state}')
        if iface_update.link_state != link_state:
            test.fail(f'Link state of interface after update should be '
                      f'{link_state}')

        vm_iface = utils_net.get_linux_ifname(session, mac)
        LOG.debug(f'Interface inside vm: {vm_iface}')

        LOG.debug('Check link state inside vm via ethtool command')
        eth_output = session.cmd_output(f'ethtool {vm_iface}')
        LOG.debug(f'ethtool output:\s{eth_output}')
        if re.search('Link\sdetected:\sno', eth_output) is None:
            test.fail('ethtool output not correct, should contain'
                      '"Link detected: no"')
        session.close()

    finally:
        bkxml.sync()
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
        if create_linux_br:
            LOG.debug(f'Delete linux bridge: {linux_br}')
            utils_net.delete_linux_bridge_tmux(linux_br, host_iface)
        if create_ovs_br:
            utils_net.delete_ovs_bridge(linux_br)
