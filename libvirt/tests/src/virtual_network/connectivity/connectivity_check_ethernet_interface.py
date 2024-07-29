import logging

import aexpect
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
    tap_mtu = params.get('tap_mtu')
    iface_mtu = params.get('iface_mtu')
    iface_attrs = eval(params.get('iface_attrs'))
    outside_ip = params.get('outside_ip')
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state="UP")[0]
    host_ip = utils_net.get_ip_address_by_interface(host_iface, ip_ver='ipv4')
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name,
                                                   virsh_instance=virsh_ins)
    bkxml = vmxml.copy()

    try:
        mac_addr = vm_xml.VMXML.get_first_mac_by_name(vm_name, virsh_ins)

        if tap_type:
            if tap_type == 'tap':
                utils_net.create_linux_bridge_tmux(bridge_name, host_iface)
                network_base.create_tap(tap_name, bridge_name, test_user)
            elif tap_type == 'macvtap':
                mac_addr = network_base.create_macvtap(tap_name, host_iface,
                                                       test_user)
            if tap_mtu:
                network_base.set_tap_mtu(tap_name, tap_mtu)

        iface_attrs['mac_address'] = mac_addr
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            vm_name, virsh_instance=virsh_ins)
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs,
                                       virsh_instance=virsh_ins)

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
        bkxml.sync(virsh_instance=virsh_ins)
        if not root:
            del virsh_ins
        if tap_type:
            network_base.delete_tap(tap_name)
            if tap_type == 'tap':
                utils_net.delete_linux_bridge_tmux(bridge_name, host_iface)
