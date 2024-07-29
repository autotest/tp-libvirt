import logging

from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test attribute 'isolated' of 'port' element of interface
    """
    vm_name = params.get('main_vm')
    vms = params.get('vms').split()
    vm, cli_vm = (env.get_vm(vm_i) for vm_i in vms)
    rand_id = utils_misc.generate_random_string(3)
    create_linux_br = 'yes' == params.get('create_linux_br', 'no')
    linux_br = 'linux_br_' + rand_id
    net_br = 'net_br_' + rand_id
    outside_ip = params.get('outside_ip')
    set_iface = 'yes' == params.get('set_iface', 'no')
    iface_source = eval(params.get('iface_source'))
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    cli_iface_attrs = eval(params.get('cli_iface_attrs', '{}'))
    net_attrs = eval(params.get('net_attrs', '{}'))
    port_attrs = eval(params.get('port_attrs', '{}'))
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state='UP')[0]

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxmls = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

    try:
        libvirt_version.is_libvirt_feature_supported(params)
        if set_iface:
            iface_attrs = {**iface_attrs, **port_attrs}
        else:
            if iface_source == 'default':
                default_net = network_xml.NetworkXML.new_from_net_dumpxml(
                    iface_source)
                bknet = default_net.copy()
                net_attrs = {**bknet.fetch_attrs(), **port_attrs}
            else:
                net_attrs = {**net_attrs, **port_attrs}

        if not (set_iface and cli_iface_attrs):
            cli_iface_attrs = iface_attrs.copy()

        if create_linux_br:
            LOG.info(f'Create linux bridge: {linux_br}')
            utils_net.create_linux_bridge_tmux(linux_br, host_iface)
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs)
            LOG.debug(f'Network xml:\n'
                      f'{virsh.net_dumpxml(net_attrs["name"]).stdout_text}')

        mac, cli_mac = list(map(vm_xml.VMXML.get_first_mac_by_name, vms))

        vmxml, cli_vmxml = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))
        [vmxml_i.del_device('interface', by_tag=True) for vmxml_i in
         [vmxml, cli_vmxml]]

        iface_attrs.update({'mac_address': mac})
        cli_iface_attrs.update({'mac_address': cli_mac})

        [libvirt_vmxml.modify_vm_device(vmxml_i, 'interface', attrs)
         for vmxml_i, attrs in [(vmxml, iface_attrs),
                                (cli_vmxml, cli_iface_attrs)]]

        [LOG.debug(f'VMXML of {vm_x}:\n{virsh.dumpxml(vm_x).stdout_text}')
         for vm_x in vms]

        [vm_i.start() for vm_i in [vm, cli_vm]]
        session, cli_session = (vm_inst.wait_for_serial_login()
                                for vm_inst in [vm, cli_vm])
        mac, cli_mac = list(map(vm_xml.VMXML.get_first_mac_by_name, vms))

        ips = network_base.get_test_ips(session, mac, cli_session, cli_mac,
                                        ip_ver='ipv4')
        ips['outside_ip'] = outside_ip
        network_base.ping_check(params, ips, session, force_ipv4=True)

        session.close()
        cli_session.close()
    finally:
        [backup_xml.sync() for backup_xml in bkxmls]
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
        if 'bknet' in locals():
            bknet.sync()
        if create_linux_br:
            utils_net.delete_linux_bridge_tmux(linux_br, host_iface)
