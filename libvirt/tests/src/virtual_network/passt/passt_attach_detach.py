import logging
import os
import shutil
import time

import aexpect
from virttest import libvirt_version
from virttest import remote
from virttest import utils_net
from virttest import utils_selinux
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.staging import utils_memory
from virttest.utils_libvirt import libvirt_unprivileged
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base
from provider.virtual_network import passt

LOG = logging.getLogger('avocado.' + __name__)

VIRSH_ARGS = {'debug': True, 'ignore_status': False}
IPV6_LENGTH = 128


def check_mac_in_domiflist(vm_name, mac, virsh_ins, test, expect=True):
    domif = virsh_ins.domiflist(vm_name, **VIRSH_ARGS).stdout_text
    found = mac in domif
    msg = f'{"" if found else "Not"} Found mac {mac} of iface '\
          f'from output of domiflist'
    if found != expect:
        test.fail(msg)


def run(test, params, env):
    """
    Test passt backend interface hotplug and hotunplug
    """
    libvirt_version.is_libvirt_feature_supported(params)
    root = 'root_user' == params.get('user_type', '')
    if root:
        vm_name = params.get('main_vm')
        vm = env.get_vm(vm_name)
        virsh_ins = virsh
        log_dir = params.get('log_dir')
        user_id = params.get('user_id')
    else:
        vm_name = params.get('unpr_vm_name')
        test_user = params.get('test_user', '')
        test_passwd = params.get('test_passwd', '')
        user_id = passt.get_user_id(test_user)
        unpr_vm_args = {
            'username': params.get('username'),
            'password': params.get('password'),
        }
        vm = libvirt_unprivileged.get_unprivileged_vm(vm_name, test_user,
                                                      test_passwd,
                                                      **unpr_vm_args)
        uri = f'qemu+ssh://{test_user}@localhost/session'
        virsh_ins = virsh.Virsh(uri=uri)
        host_session = aexpect.ShellSession('su')
        remote.VMManager.set_ssh_auth(host_session, 'localhost', test_user,
                                      test_passwd)
        host_session.close()

    scenario = params.get('scenario')
    virsh_uri = params.get('virsh_uri')
    add_iface = 'yes' == params.get('add_iface', 'no')
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True, json=True)
    host_ip = utils_net.get_ip_address_by_interface(host_iface, ip_ver='ipv4')
    host_ip_v6 = utils_net.get_ip_address_by_interface(host_iface, ip_ver='ipv6')
    iface_attrs = eval(params.get('iface_attrs'))
    params['socket_dir'] = socket_dir = eval(params.get('socket_dir'))
    params['proc_checks'] = proc_checks = eval(params.get('proc_checks', '{}'))
    mtu = params.get('mtu')
    outside_ip = params.get('outside_ip')
    log_file = f'/run/user/{user_id}/passt.log'
    iface_attrs['backend']['logFile'] = log_file
    iface_attrs['source']['dev'] = host_iface
    vhostuser = 'yes' == params.get('vhostuser', 'no')
    multiple_nexthops = 'yes' == params.get('multiple_nexthops', 'no')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name,
                                                   virsh_instance=virsh_ins)
    bkxml = vmxml.copy()
    shp_orig_num = utils_memory.get_num_huge_pages()

    selinux_status = passt.ensure_selinux_enforcing()
    passt.check_socat_installed()

    firewalld = service.Factory.create_service("firewalld")
    try:
        if root:
            passt.make_log_dir(user_id, log_dir)

        vmxml.del_device('interface', by_tag=True)
        if vhostuser:
            # set static hugepage
            utils_memory.set_num_huge_pages(2048)
            vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared", hpgs=True, vmxml=vmxml)
            # update vm xml with shared memory and vhostuser interface
            iface_attrs['type_name'] = 'vhostuser'
        iface_device = libvirt_vmxml.create_vm_device_by_type('interface',
                                                              iface_attrs)
        LOG.debug(f'iface_device: {iface_device}')
        if add_iface:
            vmxml.add_device(iface_device)
        vmxml.sync(virsh_instance=virsh_ins)
        vm.start()

        if 'attach' in scenario:
            virsh_ins.attach_device(vm_name, iface_device.xml, **VIRSH_ARGS)
            LOG.debug(virsh_ins.dumpxml(vm_name).stdout_text)

            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name,
                                                  virsh_instance=virsh_ins)
            iface_after_at = vmxml.get_devices(device_type='interface')[0]
            LOG.debug(f'iface device after detach: {iface_after_at}')
            iface_after_at.del_mac_address()
            iface_after_at.del_address()
            if iface_after_at != iface_device:
                test.fail('iface xml changed after attach')

            mac = vm.get_virsh_mac_address()
            check_mac_in_domiflist(vm_name, mac, virsh_ins, test)

            passt.check_proc_info(params, log_file, mac)

            #   check the passt log
            if not os.path.exists(log_file):
                test.fail(f'Logfile of passt "{log_file}" not created')

            # wait for the vm boot before first time to try serial login
            time.sleep(5)
            session = vm.wait_for_serial_login(timeout=60)
            vm_iface = utils_net.get_linux_ifname(session, mac)
            passt.check_vm_ip(iface_attrs, session, host_iface, vm_iface)
            passt.check_vm_mtu(session, vm_iface, mtu)
            passt.check_default_gw(session, host_iface, multiple_nexthops)
            passt.check_nameserver(session)

            ips = {
                'outside_ip': outside_ip,
                'host_public_ip': host_ip,
            }
            network_base.ping_check(params, ips, session, force_ipv4=True)

            firewalld.stop()
            LOG.debug(f'Service status of firewalld: {firewalld.status()}')
            passt.check_connection(vm, vm_iface,
                                   ['TCP4', 'TCP6', 'UDP4', 'UDP6'],
                                   host_iface)

            if 'portForwards' in iface_attrs:
                passt.check_portforward(vm, host_ip, params)

        if 'detach' not in scenario:
            vm.destroy()
            passt.check_passt_pid_not_exist()
        else:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name,
                                                  virsh_instance=virsh_ins)
            LOG.debug(f'vmxml before detach:\n{vmxml}')
            iface_to_detach = vmxml.get_devices('interface')[0]
            mac = iface_to_detach.mac_address
            # Wait for guest os to boot completely before detaching interface
            vm.wait_for_serial_login(timeout=60).close()

            virsh.detach_device(vm_name, iface_to_detach.xml,
                                wait_for_event=True, event_timeout=15,
                                uri=virsh_uri, **VIRSH_ARGS)

            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name,
                                                  virsh_instance=virsh_ins)
            LOG.debug(f'vmxml after detach: {vmxml}')
            iface_after_detach = vmxml.get_devices('interface')
            if iface_after_detach:
                test.fail('Interface should be detached from vm, but it can '
                          'still be found in vmxml')

            check_mac_in_domiflist(vm_name, mac, virsh_ins, test, expect=False)
            passt.check_passt_pid_not_exist()

    finally:
        firewalld.start()
        vm.destroy()
        bkxml.sync(virsh_instance=virsh_ins)
        if root:
            shutil.rmtree(log_dir)
        utils_selinux.set_status(selinux_status)
        utils_memory.set_num_huge_pages(shp_orig_num)
