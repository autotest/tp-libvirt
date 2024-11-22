import logging
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
from virttest.utils_libvirt import libvirt_unprivileged

from provider.virtual_network import passt

LOG = logging.getLogger('avocado.' + __name__)

VIRSH_ARGS = {'debug': True, 'ignore_status': False}
IPV6_LENGTH = 128


def run(test, params, env):
    """
    Test connectivity between 2 vms with passt backend interfaces
    """
    libvirt_version.is_libvirt_feature_supported(params)
    root = 'root_user' == params.get('user_type', '')
    if root:
        vm_name = params.get('main_vm')
        vm = env.get_vm(vm_name)
        vm_c = env.get_vm(params.get('vm_c_name'))
        virsh_ins = virsh
        log_dir = params.get('log_dir')
        user_id = params.get('user_id')
        log_file = f'/run/user/{user_id}/passt.log'
        log_file_c = f'/run/user/{user_id}/passt_c.log'
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
        vm_c = libvirt_unprivileged.get_unprivileged_vm(params.get('unpr_vm_c_name'),
                                                        test_user,
                                                        test_passwd,
                                                        **unpr_vm_args)
        uri = f'qemu+ssh://{test_user}@localhost/session'
        virsh_ins = virsh.VirshPersistent(uri=uri)
        host_session = aexpect.ShellSession('su')
        remote.VMManager.set_ssh_auth(host_session, 'localhost', test_user,
                                      test_passwd)
        host_session.close()
        log_file = f'/run/user/{user_id}/passt.log'
        log_file_c = f'/run/user/{user_id}/passt_c.log'

    virsh_uri = params.get('virsh_uri')
    iface_attrs = eval(params.get('iface_attrs'))
    iface_c_attrs = eval(params.get('iface_c_attrs'))
    params['socket_dir'] = socket_dir = eval(params.get('socket_dir'))
    params['proc_checks'] = proc_checks = eval(params.get('proc_checks', '{}'))
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    iface_attrs['backend']['logFile'] = log_file
    iface_c_attrs['backend']['logFile'] = log_file_c
    iface_attrs['source']['dev'] = host_iface
    iface_c_attrs['source']['dev'] = host_iface

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name,
                                                   virsh_instance=virsh_ins)
    vmxml_c = vm_xml.VMXML.new_from_inactive_dumpxml(vm_c.name,
                                                     virsh_instance=virsh_ins)
    bkxml = vmxml.copy()
    bkxml_c = vmxml_c.copy()

    selinux_status = passt.ensure_selinux_enforcing()
    passt.check_socat_installed()

    firewalld = service.Factory.create_service("firewalld")
    try:
        if root:
            passt.make_log_dir(user_id, log_dir)

        passt.vm_add_iface(vmxml, iface_attrs, virsh_ins)
        passt.vm_add_iface(vmxml_c, iface_c_attrs, virsh_ins)

        [vm.start() for vm in (vm, vm_c)]
        [LOG.debug(virsh.dumpxml(vm_name, uri=virsh_uri).stdout_text)
         for vm_name in (vm_name, vm_c.name)]

        # wait for the vm boot before first time to try serial login
        time.sleep(5)
        server_session = vm.wait_for_serial_login(60)
        client_session = vm_c.wait_for_serial_login(60)
        mac = vm_c.get_virsh_mac_address()
        vm_c_iface = utils_net.get_linux_ifname(client_session, mac)

        [LOG.debug(session.cmd_output('ip a'))
         for session in (server_session, client_session)]

        server_default_gw = utils_net.get_default_gateway(
            session=server_session, force_dhcp=True, json=True)
        server_default_gw_v6 = utils_net.get_default_gateway(
            session=server_session, ip_ver='ipv6', json=True)

        firewalld.stop()
        server_session.cmd('systemctl stop firewalld')

        conn_check_list = []
        for k, v in params.items():
            if k.startswith('conn_check_args_'):
                conn_check_list.append(eval(v))
        LOG.debug(f'conn_check_list: {conn_check_list}')

        for args in conn_check_list:
            passt.check_protocol_connection(client_session, server_session,
                                            *args)
        server_session.close()
        client_session.close()
    finally:
        firewalld.start()
        vm.destroy()
        vm_c.destroy()
        bkxml.sync(virsh_instance=virsh_ins)
        bkxml_c.sync(virsh_instance=virsh_ins)
        if root:
            shutil.rmtree(log_dir)
        else:
            del virsh_ins
        utils_selinux.set_status(selinux_status)
