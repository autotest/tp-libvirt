import logging

from virttest import libvirt_version
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
    Test update of port isolated on interface with update-device command
    """
    def setup_vms_ifaces(vmxml, cli_vmxml):
        """
        Setup interface of vm xmls

        :param vmxml: vmxml instance
        :param cli_vmxml: client vm xml instance
        """
        for vmxml_i in [vmxml, cli_vmxml]:
            vmxml_i.del_device('interface', by_tag=True)

        mac, cli_mac = list(map(vm_xml.VMXML.get_first_mac_by_name, vms))
        iface_attrs.update({'mac_address': mac})
        cli_iface_attrs.update({'mac_address': cli_mac})

        for vmxml_i, attrs in [(vmxml, iface_attrs),
                               (cli_vmxml, cli_iface_attrs)]:
            libvirt_vmxml.modify_vm_device(vmxml_i, 'interface', attrs)

        for vm_x in vms:
            LOG.debug(f'VMXML of {vm_x}:\n{virsh.dumpxml(vm_x).stdout_text}')

        for vm_i in [vm, cli_vm]:
            vm_i.start()

    def get_ips(session, cli_session):
        """
        Get test ips

        :param session: vm session
        :param cli_session: client vm session
        :return: test ips
        """
        mac, cli_mac = list(map(vm_xml.VMXML.get_first_mac_by_name, vms))
        ips = network_base.get_test_ips(session, mac, cli_session, cli_mac,
                                        ip_ver='ipv4')
        ips['outside_ip'] = outside_ip

        return ips

    def test_ping(msg, ips=None):
        """
        Test ping function

        :param msg: message to log
        :param ips: test ips, defaults to None
        :return: test ips if not given
        """
        session, cli_session = (vm_inst.wait_for_serial_login()
                                for vm_inst in [vm, cli_vm])
        if ips is None:
            ips = get_ips(session, cli_session)

        LOG.info(f'Ping check {msg}')
        network_base.ping_check(params, ips, session, force_ipv4=True)

        session.close()
        cli_session.close()

        if ips:
            return ips

    def test_update_iface():
        """
        Test update interface
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface = vmxml.get_devices('interface')[0]
        LOG.debug(f'Interface xml on vm:\n{iface}')

        iface.del_port()
        iface.setup_attrs(**update_port_attrs)
        LOG.debug(f'Interface xml to update:\n{iface}')

        virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_update = vmxml.get_devices('interface')[0]
        LOG.debug(f'Interface after update:\n{iface_update}')
        if iface_update.fetch_attrs().get('port') == update_port_attrs.get('port'):
            LOG.debug('port isolated value check in xml after update: PASS')
        else:
            test.fail('port isolated value not updated in vmxml')

    vm_name = params.get('main_vm')
    vms = params.get('vms').split()
    vm, cli_vm = (env.get_vm(vm_i) for vm_i in vms)
    rand_id = utils_misc.generate_random_string(3)
    create_linux_br = 'yes' == params.get('create_linux_br', 'no')
    linux_br = 'linux_br_' + rand_id
    net_br = 'net_br_' + rand_id
    outside_ip = params.get('outside_ip')
    iface_source = eval(params.get('iface_source'))
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    cli_iface_attrs = eval(params.get('cli_iface_attrs', '{}'))
    net_attrs = eval(params.get('net_attrs', '{}'))
    update_port_attrs = eval(params.get('update_port_attrs', '{}'))
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxmls = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

    try:
        libvirt_version.is_libvirt_feature_supported(params)
        if create_linux_br:
            LOG.info(f'Create linux bridge: {linux_br}')
            utils_net.create_linux_bridge_tmux(linux_br, host_iface)
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs)
            LOG.debug(f'Network xml:\n'
                      f'{virsh.net_dumpxml(net_attrs["name"]).stdout_text}')

        vmxml, cli_vmxml = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

        setup_vms_ifaces(vmxml, cli_vmxml)
        ips = test_ping('before update', ips=None)
        test_update_iface()
        params['vm_ping_ep_vm'] = params.get('ping_after_update')
        test_ping('After update', ips)

    finally:
        for backup_xml in bkxmls:
            backup_xml.sync()
        if net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
        if create_linux_br:
            utils_net.delete_linux_bridge_tmux(linux_br, host_iface)
