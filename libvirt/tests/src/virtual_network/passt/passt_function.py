import logging
import os
import shutil

import aexpect
from virttest import libvirt_version
from virttest import remote
from virttest import utils_net
from virttest import utils_selinux
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.utils_libvirt import libvirt_unprivileged

from provider.virtual_network import network_base
from provider.virtual_network import passt

LOG = logging.getLogger('avocado.' + __name__)

IPV6_LENGTH = 128


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
        virsh_ins = virsh.VirshPersistent(uri=uri)
        host_session = aexpect.ShellSession('su')
        remote.VMManager.set_ssh_auth(host_session, 'localhost', test_user,
                                      test_passwd)
        host_session.close()

    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state="UP")[0]
    host_ip = utils_net.get_ip_address_by_interface(host_iface, ip_ver='ipv4')
    host_ip_v6 = utils_net.get_ip_address_by_interface(host_iface, ip_ver='ipv6')
    params['host_ip_v6'] = host_ip_v6
    params['socket_dir'] = socket_dir = eval(params.get('socket_dir'))
    params['proc_checks'] = proc_checks = eval(params.get('proc_checks', '{}'))
    mtu = params.get('mtu')
    outside_ip = params.get('outside_ip')
    log_file = f'/run/user/{user_id}/passt.log'
    iface_attrs = eval(params.get('iface_attrs'))
    iface_attrs['backend']['logFile'] = log_file

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name,
                                                   virsh_instance=virsh_ins)
    bkxml = vmxml.copy()

    selinux_status = passt.ensure_selinux_enforcing()
    passt.check_socat_installed()

    firewalld = service.Factory.create_service("firewalld")
    try:
        if root:
            passt.make_log_dir(user_id, log_dir)

        default_gw = utils_net.get_default_gateway()
        default_gw_v6 = utils_net.get_default_gateway(ip_ver='ipv6')

        passt.vm_add_iface(vmxml, iface_attrs, virsh_ins)
        LOG.debug(virsh_ins.dumpxml(vm_name).stdout_text)
        mac = vm.get_virsh_mac_address()

        vm.start()
        passt.check_proc_info(params, log_file, mac)

        #   check the passt log
        if not os.path.exists(log_file):
            test.fail(f'Logfile of passt "{log_file}" not created')

        session = vm.wait_for_serial_login(timeout=60)
        vm_iface = utils_net.get_linux_ifname(session, mac)
        passt.check_vm_ip(iface_attrs, session, host_iface, vm_iface)
        passt.check_vm_mtu(session, vm_iface, mtu)
        passt.check_default_gw(session, host_iface)
        passt.check_nameserver(session)

        ips = {
            'outside_ip': outside_ip,
            'host_public_ip': host_ip,
        }
        network_base.ping_check(params, ips, session, force_ipv4=True)
        session.close()

        firewalld.stop()
        LOG.debug(f'Status of firewalld: {firewalld.status()}')
        passt.check_connection(vm, vm_iface,
                               ['TCP4', 'TCP6', 'UDP4', 'UDP6'],
                               host_iface)

        if 'portForwards' in iface_attrs:
            passt.check_portforward(vm, host_ip, params, host_iface)

        vm_sess = vm.wait_for_serial_login(timeout=60)
        vm_sess.cmd('systemctl start firewalld')
        vm.destroy()

        passt.check_passt_pid_not_exist()
        if os.listdir(socket_dir):
            test.fail(f'Socket dir is not empty: {os.listdir(socket_dir)}')

    finally:
        firewalld.start()
        bkxml.sync(virsh_instance=virsh_ins)
        if root:
            shutil.rmtree(log_dir)
        else:
            del virsh_ins
        utils_selinux.set_status(selinux_status)
