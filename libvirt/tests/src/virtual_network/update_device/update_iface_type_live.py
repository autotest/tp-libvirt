import logging
import re

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml

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
    linux_br = 'linux_br_' + rand_id
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
        libvirt_network.create_or_del_network(net_attrs)
        LOG.debug(f'Network xml:\n'
                  f'{virsh.net_dumpxml(net_attrs["name"]).stdout_text}')

        vm.start()
        session = vm.wait_for_serial_login()

        iface = libvirt_vmxml.create_vm_device_by_type(
            'interface', iface_attrs)
        LOG.debug(f'Interface to attach:\n{iface}')

        virsh.attach_device(vm_name, iface.xml, **VIRSH_ARGS)

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_at = vmxml.get_devices('interface')[0]
        LOG.debug(f'Interface xml after attached to vm:\n{iface_at}')
        LOG.debug(f'Interface type after attached to vm:\n'
                  f'{iface_at.type_name}')
        if iface_at.type_name != iface_type:
            test.fail(f'Interface type should change to {iface_type}')

        LOG.debug('Update interface link state')
        link_state = 'down'
        iface.setup_attrs(**{'link_state': link_state})
        LOG.debug(iface)

        virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_update = vmxml.get_devices('interface')[0]
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
