import logging
import os
import shutil
import time

import aexpect
from avocado.utils import process
from virttest import libvirt_version
from virttest import remote
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_selinux
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.utils_libvirt import libvirt_unprivileged
from virttest.utils_test import libvirt

from provider.virtual_network import network_base
from provider.virtual_network import passt

LOG = logging.getLogger('avocado.' + __name__)

VIRSH_ARGS = {'debug': True, 'ignore_status': False}
IPV6_LENGTH = 128


def run(test, params, env):
    """
    Test passt backend interface reconnect function
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

    host_ip = utils_net.get_host_ip_address(ip_ver='ipv4')
    iface_attrs = eval(params.get('iface_attrs'))
    params['socket_dir'] = socket_dir = eval(params.get('socket_dir'))
    params['proc_checks'] = proc_checks = eval(params.get('proc_checks', '{}'))
    mtu = params.get('mtu')
    qemu_cmd_check = params.get('qemu_cmd_check')
    outside_ip = params.get('outside_ip')
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    log_file = f'/run/user/{user_id}/passt.log'
    iface_attrs['backend']['logFile'] = log_file
    iface_attrs['source']['dev'] = host_iface

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name,
                                                   virsh_instance=virsh_ins)
    bkxml = vmxml.copy()

    selinux_status = passt.ensure_selinux_enforcing()
    passt.check_socat_installed()

    firewalld = service.Factory.create_service("firewalld")
    try:
        if root:
            passt.make_log_dir(user_id, log_dir)

        passt.vm_add_iface(vmxml, iface_attrs, virsh_ins)
        vm.start()

        libvirt.check_qemu_cmd_line(qemu_cmd_check)

        passt_proc = passt.get_proc_info('passt')
        LOG.debug(f'passt process info: {passt_proc}')

        pid_passt = process.run('pidof passt',
                                ignore_status=True).stdout_text.strip()
        process.run(f'kill -9 {pid_passt}', shell=True)

        utils_misc.wait_for(lambda: passt.get_proc_info('passt'), 30,
                            ignore_errors=True)
        passt_proc_r = passt.get_proc_info('passt')
        LOG.debug(f'passt process info after reconnect: {passt_proc_r}')

        if passt_proc['CMD'] != passt_proc_r['CMD']:
            test.fail('passt process info changed after reconnect')

        mac = vm.get_virsh_mac_address()
        passt.check_proc_info(params, log_file, mac)

        if not os.path.exists(log_file):
            test.fail(f'Logfile of passt "{log_file}" not created')

        # wait for the vm boot before first time to try serial login
        time.sleep(5)
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

        firewalld.stop()
        LOG.debug(f'Service status of firewalld: {firewalld.status()}')

        passt.check_connection(vm, vm_iface,
                               ['TCP4', 'TCP6', 'UDP4', 'UDP6'],
                               host_iface)

        if 'portForwards' in iface_attrs:
            passt.check_portforward(vm, host_ip, params)

        vm.destroy()
        passt.check_passt_pid_not_exist()
    finally:
        firewalld.start()
        vm.destroy()
        bkxml.sync(virsh_instance=virsh_ins)
        if root:
            shutil.rmtree(log_dir)
        utils_selinux.set_status(selinux_status)
