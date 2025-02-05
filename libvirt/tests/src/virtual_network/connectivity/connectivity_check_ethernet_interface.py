import logging

import aexpect
from avocado.utils import process
from virttest import libvirt_version
from virttest import remote
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_unprivileged
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.virtual_network import network_base

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test passt function
    """
    libvirt_version.is_libvirt_feature_supported(params)
    root = 'root_user' == params.get('user_type', '')
    if root:
        vm_name = params.get('main_vm')
        vm = env.get_vm(vm_name)
        virsh_ins = virsh
        test_user = 'root'
    else:
        vm_name = params.get('unpr_vm_name')
        test_user = params.get('test_user', '')
        test_passwd = params.get('test_passwd', '')
        unpr_vm_args = {
            'username': params.get('username'),
            'password': params.get('password'),
        }
        vm = libvirt_unprivileged.get_unprivileged_vm(vm_name, test_user,
                                                      test_passwd,
                                                      **unpr_vm_args)
        uri = f'qemu+ssh://{test_user}@localhost/session'
        virsh_ins = virsh.VirshPersistent(uri=uri)
        host_session = aexpect.ShellSession('su')
        remote.VMManager.set_ssh_auth(host_session, 'localhost', test_user,
                                      test_passwd)
        host_session.close()

    rand_id = utils_misc.generate_random_string(3)
    bridge_name = 'br_' + rand_id
    tap_type = params.get('tap_type', '')
    tap_name = tap_type + '_' + rand_id
    tap_name_2 = tap_name + '_2'
    tap_mtu = params.get('tap_mtu')
    iface_mtu = params.get('iface_mtu')
    iface_mtu_2 = params.get('iface_mtu_2')
    iface_attrs = eval(params.get('iface_attrs'))
    iface_attrs_2 = eval(params.get('iface_attrs_2', '{}'))
    iface_amount = params.get('iface_amount')
    outside_ip = params.get('outside_ip')
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True, json=True).split()[0]
    host_ip = utils_net.get_ip_address_by_interface(host_iface, ip_ver='ipv4')
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name,
                                                   virsh_instance=virsh_ins)
    bkxml = vmxml.copy()

    try:
        mac_addr = vm_xml.VMXML.get_first_mac_by_name(vm_name, virsh_ins)
        tap_flag = ''
        iface_ori = network_base.get_iface_xml_inst(
            vm_name, 'on vm', virsh_ins=virsh_ins)
        for attr in (iface_attrs, iface_attrs_2, iface_ori.fetch_attrs()):
            if attr.get('driver', {}).get('driver_attr', {}).get('queues'):
                tap_flag = 'multi_queue'
                break
        if params.get('tap_flag') is not None:
            tap_flag = params.get('tap_flag')

        if tap_type:
            if tap_type == 'tap':
                utils_net.create_linux_bridge_tmux(bridge_name, host_iface)
                network_base.create_tap(
                    tap_name, bridge_name, test_user, flag=tap_flag)
                if iface_amount == 'two_ifaces':
                    network_base.create_tap(
                        tap_name_2, 'virbr0', test_user, flag=tap_flag)
            elif tap_type == 'macvtap':
                mac_addr = network_base.create_macvtap(tap_name, host_iface,
                                                       test_user)
            if tap_mtu:
                network_base.set_tap_mtu(tap_name, tap_mtu)
                if iface_amount == 'two_ifaces':
                    network_base.set_tap_mtu(tap_name_2, tap_mtu)

        process.run('ip a')

        iface_attrs['mac_address'] = mac_addr
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            vm_name, virsh_instance=virsh_ins)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs,
                                       virsh_instance=virsh_ins)
        if iface_attrs_2:
            mac_addr_2 = utils_net.generate_mac_address_simple()
            iface_attrs_2['mac_address'] = mac_addr_2
            LOG.debug('Adding a 2nd interface to vm xml')
            libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs_2, 1,
                                           virsh_instance=virsh_ins)

        LOG.debug(f'VMxml is:\n{virsh.dumpxml(vm_name, uri=uri).stdout_text}')

        start_result = virsh.start(vm_name, debug=True, uri=uri)
        libvirt.check_exit_status(start_result, status_error)

        if status_error:
            libvirt.check_result(start_result, err_msg)
            return

        vm.create_serial_console()
        session = vm.wait_for_serial_login(60)

        ips = {
            'outside_ip': outside_ip,
            'host_public_ip': host_ip,
        }
        vm_default_gw = utils_net.get_default_gateway(session=session, json=True)
        if isinstance(vm_default_gw, list):
            vm_default_gw = [gw for gw in vm_default_gw if gw.startswith('192')][0]
        LOG.debug(f'vm_default_gw is {vm_default_gw}')

        if iface_amount == 'two_ifaces':
            ping_params = {
                mac_addr:
                {'vm_ping_outside': params.get('vm_ping_outside'),
                 'vm_ping_host_public': params.get('vm_ping_host_public'), },
                mac_addr_2:
                {'vm_ping_outside': params.get('vm_ping_outside')}
            }
            ips_2 = {
                mac_addr: ips,
                mac_addr_2: {'outside_ip': vm_default_gw}
            }
            for mac in (mac_addr, mac_addr_2):
                iface_vm_info = utils_net.get_linux_iface_info(
                    mac=mac, session=session)
                iface_vm = iface_vm_info['ifname']
                LOG.debug(f'Check connetivity of iface {iface_vm}({mac})')
                ping_args = {
                    'vm_ping_outside': {'interface': iface_vm},
                    'vm_ping_host_public': {'interface': iface_vm},
                }
                network_base.ping_check(ping_params[mac], ips_2[mac], session,
                                        force_ipv4=True, **ping_args)
        else:
            network_base.ping_check(params, ips, session, force_ipv4=True)

        if iface_mtu:
            # Check mtu of live vmxml
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name,
                                                  virsh_instance=virsh_ins)
            iface = vmxml.devices.by_device_tag("interface")[0]
            vmxml_mtu = iface['mtu']['size']
            if int(vmxml_mtu) != int(iface_mtu):
                test.fail(f'MTU in vmxml was set to be {iface_mtu}, '
                          f'but actually got {vmxml_mtu}')
            else:
                LOG.info('MTU check of vmxml PASS')

            # Check mtu inside vm
            vm_iface = interface_base.get_vm_iface(session)
            vm_iface_info = utils_net.get_linux_iface_info(iface=vm_iface,
                                                           session=session)
            vm_mtu = vm_iface_info['mtu']
            if int(vm_mtu) != int(iface_mtu):
                test.fail(f'MTU of interface inside vm should be '
                          f'{iface_mtu}, not {vm_mtu}')
            else:
                LOG.info('MTU check inside vm PASS')

        if tap_mtu:
            host_tap_info = utils_net.get_linux_iface_info(
                iface=tap_name)
            host_mtu = host_tap_info['mtu']
            if int(host_mtu) != int(tap_mtu):
                test.fail(f'MTU of host tap device should be '
                          f'{tap_mtu}, not {host_mtu}')
            else:
                LOG.info('MTU check on host PASS')
        session.close()

    finally:
        if tap_type:
            network_base.delete_tap(tap_name)
            if iface_amount == 'two_ifaces':
                network_base.delete_tap(tap_name_2)
            if tap_type == 'tap':
                utils_net.delete_linux_bridge_tmux(bridge_name, host_iface)
        bkxml.sync(virsh_instance=virsh_ins)
        if not root:
            virsh_ins.close_session()
