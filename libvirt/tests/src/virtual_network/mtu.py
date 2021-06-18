import logging
import os
import time
import shutil

from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.utils_test import libvirt

DEFAULT_NET = 'default'


def run(test, params, env):
    """
    Test mtu feature from virtual network
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    mtu_type = params.get('mtu_type')
    mtu_size = params.get('mtu_size', '')
    net = params.get('net', DEFAULT_NET)
    net_type = params.get('net_type', '')
    with_iface = 'yes' == params.get('with_iface', 'no')
    with_net = 'yes' == params.get('with_net', 'no')
    status_error = 'yes' == params.get('status_error', 'no')
    check = params.get('check', '')
    error_msg = params.get('error_msg', '')
    bridge_name = 'br_mtu' + utils_misc.generate_random_string(3)
    add_pkg = params.get('add_pkg', '')
    model = params.get('model', 'virtio')
    timeout = int(params.get('timeout', 240))
    wait_for_up = int(params.get('wait_for_up', 0))
    create_tap = 'yes' == params.get('create_tap', 'no')
    tap_mtu_size = int(params.get('tap_mtu_size', 2000))

    if create_tap and 'managed_no' in params['name']:
        if not libvirt_version.version_compare(7, 0, 0):
            test.cancel('This test is not supported until libvirt-7.0.0')

    def set_network(size, net='default'):
        """
        Set mtu size to a certain network
        """
        logging.info('Set mtu size of network "%s" to %s', net, size)
        default_xml = NetworkXML.new_from_net_dumpxml(net)
        default_xml.mtu = size
        default_xml.sync()
        logging.debug(virsh.net_dumpxml(net))

    def set_interface(mtu_size='', source_network='default',
                      iface_type='network', iface_model='virtio',
                      iface_target=None):
        """
        Set mtu size to a certain interface
        """
        interface_type = 'bridge' if iface_type in ('bridge', 'openvswitch') else iface_type
        iface_dict = {
            'type': interface_type,
            'model': iface_model
        }
        if source_network:
            iface_dict.update({'source': "{'%s': '%s'}" % (interface_type, source_network)})

        if iface_type == 'openvswitch':
            iface_dict.update({'virtualport_type': 'openvswitch'})

        if mtu_size:
            iface_dict.update({'mtu': "{'size': %s}" % mtu_size})

        if iface_target:
            iface_dict.update({'target': iface_target})

        libvirt.modify_vm_iface(vm_name, 'update_iface', iface_dict)
        logging.debug(virsh.dumpxml(vm_name).stdout)

    def get_default_if():
        """
        Get default interface that is using by vm
        """
        ifaces = utils_net.get_sorted_net_if()
        logging.debug('Interfaces on host: %s', ifaces)
        for iface in ifaces[0]:
            if 'Link detected: yes' in process.run('ethtool %s' % iface).stdout_text:
                logging.debug('Found host interface "%s"', iface)
                return iface

    def create_bridge():
        """
        Create a bridge on host for test
        """
        cmd_create_br = 'nmcli con add type bridge con-name %s ifname %s'
        con_name = 'con_' + utils_misc.generate_random_string(3)
        bridge_name = 'br_' + utils_misc.generate_random_string(3)
        process.run(cmd_create_br % (con_name, bridge_name), verbose=True)
        return con_name, bridge_name

    def create_network_xml(name, network_type, base_if='', **kwargs):
        """
        Create a network xml to be defined
        """
        m_net = NetworkXML(name)
        m_net.forward = {'mode': 'bridge'}
        if network_type in ('bridge', 'openvswitch'):
            m_net.bridge = {'name': kwargs['bridge_name']}
        elif network_type == 'macvtap':
            if base_if:
                m_net.forward_interface = [{'dev': base_if}]
        if network_type == 'openvswitch':
            m_net.virtualport_type = 'openvswitch'
        if 'mtu' in kwargs:
            m_net.mtu = kwargs['mtu']
        logging.debug(m_net)
        libvirt.wait_for_file_over("</network>", m_net.xml)
        return m_net.xml

    def create_iface(iface_type, **kwargs):
        """
        Create a interface to be attached to vm
        """
        m_iface = Interface(iface_type)
        m_iface.mac_address = utils_net.generate_mac_address_simple()
        if 'base_if' in kwargs:
            m_iface.source = {'dev': kwargs['base_if'],
                              'mode': 'vepa'}
        if 'source_net' in kwargs:
            m_iface.source = {'network': kwargs['source_net']}
        if 'mtu' in kwargs:
            m_iface.mtu = {'size': kwargs['mtu']}
        if 'model_net' in kwargs:
            m_iface.model = kwargs['model_net']
        logging.debug(m_iface.get_xml())
        logging.debug(m_iface)
        return m_iface

    def check_mtu(mtu_size, qemu=False):
        """
        Check if mtu meets expectation on host
        """
        error = ''
        live_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_xml = live_vmxml.get_devices('interface')[0]
        logging.debug(iface_xml.target)
        dev = iface_xml.target['dev']
        ifconfig_info = process.run('ifconfig|grep mtu|grep %s' % dev,
                                    shell=True, verbose=True).stdout_text
        if 'mtu %s' % mtu_size in ifconfig_info:
            logging.info('PASS on ifconfig check for vnet.')
        else:
            error += 'Fail on ifconfig check for vnet.'
        if qemu:
            qemu_mtu_info = process.run('ps aux|grep qemu-kvm',
                                        shell=True, verbose=True).stdout_text
            if 'host_mtu=%s' % mtu_size in qemu_mtu_info:
                logging.info('PASS on qemu cmd line check.')
            else:
                error += 'Fail on qemu cmd line check.'
        if error:
            test.fail(error)

    def check_mtu_in_vm(fn_login, mtu_size, timeout):
        """
        Check if mtu meets expectations in vm
        """
        session = fn_login(timeout=timeout)
        check_cmd = 'ifconfig'
        output = session.cmd(check_cmd)
        session.close()
        logging.debug(output)
        if 'mtu %s' % mtu_size not in output:
            test.fail('MTU check inside vm failed.')
        else:
            logging.debug("MTU check inside vm passed.")

    try:
        bk_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        bk_netxml = NetworkXML.new_from_net_dumpxml(DEFAULT_NET)
        if add_pkg:
            add_pkg = add_pkg.split()
            new_pkg = add_pkg.copy()
            if 'openvswitch' in add_pkg and shutil.which('ovs-vsctl'):
                new_pkg.remove('openvswitch')
            utils_package.package_install(new_pkg)
        if 'openvswitch' in add_pkg:
            br = 'ovsbr0' + utils_misc.generate_random_string(3)
            process.run('systemctl start openvswitch.service', shell=True, verbose=True)
            process.run('ovs-vsctl add-br %s' % br, shell=True, verbose=True)
            process.run('ovs-vsctl show', shell=True, verbose=True)

        if create_tap:
            tap_name = 'tap_' + utils_misc.generate_random_string(3)
            tap_cmd = params.get('tap_cmd')
            if tap_cmd is None:
                test.error('No tap creating command provided.')
            tap_cmd = tap_cmd.format(tap_name=tap_name)
            logging.debug('Tap creating command: \n %s', tap_cmd)
            # Create tap device
            process.run(tap_cmd, verbose=True, shell=True)
            # Check tap device's detail
            ip_link_cmd = 'ip link show %s'
            process.run(ip_link_cmd % tap_name, verbose=True)

            if 'managed_no' in params['name']:
                iface_target = params.get('iface_target') % tap_name
                set_interface(mtu_size, source_network=None,
                              iface_type='ethernet', iface_target=iface_target)
                vm.start()
                logging.debug(virsh.dumpxml(vm_name).stdout)

                # Check mtu of tap on host
                host_iface_info = process.run(ip_link_cmd % tap_name, verbose=True).stdout_text
                if 'mtu %s' % tap_mtu_size in host_iface_info:
                    logging.info('Host mtu size check PASS.')
                else:
                    test.fail('Host mtu size check FAIL.')

                # Get iface mac address
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                iface = vmxml.devices.by_device_tag('interface')[0]
                iface_mac = iface.mac_address

                # Check mtu inside vm
                session = vm.wait_for_serial_login()
                iface_in_vm = utils_net.get_linux_ifname(session, mac_address=iface_mac)
                vm_iface_info = session.cmd(ip_link_cmd % iface_in_vm)
                session.close()
                logging.debug(vm_iface_info)
                if 'mtu %s' % mtu_size in vm_iface_info:
                    logging.info('Inside-vm mtu size check PASS.')
                else:
                    test.fail('Inside-vm mtu size check FAIL.')

        elif not check or check in ['save', 'managedsave', 'hotplug_save']:
            # Create bridge or network and set mtu
            iface_type = 'network'
            if net_type in ('bridge', 'openvswitch'):
                if net_type == 'bridge':
                    params['con_name'], br = create_bridge()
                if mtu_type == 'network':
                    test_net = create_network_xml(
                        bridge_name, net_type,
                        bridge_name=br
                    )
                    virsh.net_create(test_net, debug=True)
                    virsh.net_dumpxml(bridge_name, debug=True)
                if mtu_type == 'interface':
                    iface_type = net_type
                    bridge_name = br
            elif net_type == 'network':
                if mtu_type == 'network':
                    set_network(mtu_size)

            iface_mtu = 0
            if mtu_type == 'interface':
                iface_mtu = mtu_size
            if mtu_type == 'network' and with_iface:
                mtu_size = str(int(mtu_size)//2)
                iface_mtu = mtu_size

            source_net = bridge_name if net_type in ('bridge', 'openvswitch') else 'default'

            # set mtu in vm interface
            set_interface(iface_mtu, source_network=source_net, iface_type=iface_type, iface_model=model)
            vm.start()
            vm_login = vm.wait_for_serial_login if net_type in ('bridge', 'openvswitch') else vm.wait_for_login
            vm_login().close()
            check_qemu = True if mtu_type == 'interface' else False

            # Test mtu after save vm
            if check in ('save', 'hotplug_save'):
                if check == 'hotplug_save':
                    iface = create_iface('network', source_net='default',
                                         mtu=mtu_size, model_net=model)
                    params['mac'] = iface.mac_address
                    virsh.attach_device(vm_name, iface.xml, debug=True)
                    virsh.dumpxml(vm_name, debug=True)
                    dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                    if params['mac'] not in str(dom_xml):
                        test.fail('Failed to attach interface with mtu')
                save_path = os.path.join(data_dir.get_tmp_dir(), vm_name + '.save')
                time.sleep(wait_for_up)
                virsh.save(vm_name, save_path, debug=True)
                virsh.restore(save_path, debug=True)
            if check == 'managedsave':
                virsh.managedsave(vm_name, debug=True)
                virsh.start(vm_name, debug=True)

            # Check in both host and vm
            check_mtu(mtu_size, check_qemu)
            if mtu_type == 'interface' or with_iface:
                check_mtu_in_vm(vm_login, mtu_size, timeout=timeout)
                vm_login(timeout=timeout).close()

            if check == 'hotplug_save':
                virsh.detach_interface(vm_name, 'network %s' % params['mac'], debug=True)
                time.sleep(5)
                dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                if params['mac'] in str(dom_xml):
                    test.fail('Failed to detach interface with mtu after save-restore')

        else:
            hotplug = 'yes' == params.get('hotplug', 'False')
            if check == 'net_update':
                result = virsh.net_update(
                    DEFAULT_NET, 'modify', 'mtu',
                    '''"<mtu size='%s'/>"''' % mtu_size,
                    debug=True
                )
            if check in ('macvtap', 'bridge_net', 'ovswitch_net'):
                base_if = get_default_if()
                macv_name = 'direct-macvtap' + utils_misc.generate_random_string(3)

                # Test mtu in different type of network
                if mtu_type == 'network':
                    if check == 'macvtap':
                        test_net = create_network_xml(macv_name, 'macvtap',
                                                      base_if, mtu=mtu_size)
                    if check == 'bridge_net':
                        params['con_name'], br = create_bridge()
                        test_net = create_network_xml(
                            bridge_name, 'bridge', mtu=mtu_size,
                            bridge_name=br
                        )
                    if check == 'ovswitch_net':
                        test_net = create_network_xml(
                            bridge_name, 'openvswitch', mtu=mtu_size,
                            bridge_name=br
                        )
                    if 'net_create' in params['id']:
                        result = virsh.net_create(test_net, debug=True)
                    if 'net_define' in params['id']:
                        result = virsh.net_define(test_net, debug=True)

                # Test mtu with or without a binding network
                elif mtu_type == 'interface':
                    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
                    if with_net:
                        test_net = create_network_xml(macv_name, 'macvtap', base_if)
                        virsh.net_create(test_net, debug=True)
                        iface = create_iface('network', source_net=macv_name, mtu=mtu_size, model_net=model)
                        if hotplug:
                            result = virsh.attach_device(vm_name, iface.xml, debug=True)
                        else:
                            vmxml.add_device(iface)
                            vmxml.sync()
                            result = virsh.start(vm_name)
                    else:
                        iface = create_iface('direct', base_if=base_if, mtu=mtu_size, model_net=model)
                        if hotplug:
                            result = virsh.attach_device(vm_name, iface.xml, debug=True)
                        else:
                            vmxml.add_device(iface)
                            result = virsh.define(vmxml.xml, debug=True)
            if check == 'invalid_val':
                iface = create_iface('network', source_net='default', mtu=mtu_size, model_net=model)
                result = virsh.attach_device(vm_name, iface.xml, debug=True)

            # Check result
            libvirt.check_exit_status(result, status_error)
            libvirt.check_result(result, [error_msg])

    finally:
        bk_xml.sync()
        bk_netxml.sync()
        if 'test_net' in locals():
            virsh.net_destroy(bridge_name, debug=True)
        if params.get('con_name'):
            process.run('nmcli con del %s' % params['con_name'], verbose=True)
        if add_pkg:
            process.run("ovs-vsctl del-br %s" % br, verbose=True)
            utils_package.package_remove(add_pkg)
        if create_tap:
            process.run('ip tuntap del mode tap {}'.format(tap_name), verbose=True, shell=True)
