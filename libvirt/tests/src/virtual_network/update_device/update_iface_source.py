import logging

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test live update interface source setting
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    rand_id = utils_misc.generate_random_string(3)
    net_name = 'net_' + rand_id
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    net_attrs = eval(params.get('net_attrs', '{}'))
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    ips = {'outside_ip': params.get('outside_ip')}

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs)
            net_xml = network_xml.NetworkXML.new_from_net_dumpxml(
                net_attrs["name"])
            net_xml.debug_xml()
            if net_xml.forward['mode'] == 'nat':
                net_br = net_xml.bridge['name']

        vm.start()
        session = vm.wait_for_serial_login()
        network_base.ping_check(params, ips, session, force_ipv4=True)

        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        update_attrs = eval(params.get('update_attrs', '{}'))
        LOG.debug(f'Update iface with attrs: {update_attrs}')
        iface.setup_attrs(**update_attrs)
        LOG.debug(f'Update iface with xml:\n{iface}')

        up_result = virsh.update_device(vm_name, iface.xml, debug=True)
        libvirt.check_exit_status(up_result, status_error)
        if err_msg:
            libvirt.check_result(up_result, err_msg)
            return

        iface_update = network_base.get_iface_xml_inst(vm_name, 'after update')
        LOG.debug(f'iface source after update: {iface_update.source}')
        if not iface_update.source.items() >= update_attrs['source'].items():
            test.fail('Source of interface xml not correctly updated')

        mac = iface.mac_address
        vm_iface = utils_net.get_linux_ifname(session, mac)
        LOG.debug(f'Interface inside vm: {vm_iface}')

        LOG.info('Run dhclient to refresh network.')
        utils_net.restart_guest_network(session, mac)

        vm_iface_info = utils_net.get_linux_iface_info(
            vm_iface, session=session)
        LOG.debug(f'iface info inside vm:\n{vm_iface_info}')

        network_base.ping_check(params, ips, session, force_ipv4=True)

    finally:
        bkxml.sync()
        if 'session' in locals():
            session.close()
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
