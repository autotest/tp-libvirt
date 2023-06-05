import logging
import os
import shutil
import time

import aexpect
from avocado.utils import process
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


def transfer_host_to_vm(session, prot, ip_ver, params, test):
    """
    Test file transfer from host to vm via socat

    :param session: vm session
    :param prot: protocol
    :param ip_ver: ip version
    :param params: test params
    :param test: test instance
    """
    test_file = params.get('test_file')
    rec_file = params.get('rec_file')
    cmd_create_file = params.get('cmd_create_file')
    port = params.get('port')
    cmd_listen = eval(params.get('cmd_listen'))
    process.run(cmd_create_file, shell=True)
    md5 = process.run(f'md5sum {test_file}',
                      shell=True).stdout_text.split()[0]
    LOG.debug(f'MD5 of sent file: {md5}')
    session.sendline('systemctl stop firewalld')
    session.sendline(cmd_listen)
    time.sleep(5)
    addr = 'localhost' if ip_ver == 'ipv4' else '[::1]'
    cmd_transfer = eval(params.get('cmd_transfer'))
    process.run(cmd_transfer, shell=True)

    if session.cmd_status(f'test -a {rec_file}') != 0:
        test.fail(f'VM did not recieve {rec_file}')
    vm_file_md5 = session.cmd_output(f'md5sum {rec_file}')
    vm_file_md5 = vm_file_md5.split()[0]
    LOG.debug(f'MD5 of recieved file: {vm_file_md5}')
    try:
        if md5 == vm_file_md5:
            LOG.debug('MD5 check PASS')
        else:
            test.fail('MD5 of files from host and vm NOT match')
    finally:
        session.cmd(f'rm {rec_file} -f')


def transfer_vm_to_host(session, prot, ip_ver, vm_iface, firewalld,
                        params, test):
    """
    Test file transfer from vm to host via socat

    :param session: vm session
    :param prot: protocol
    :param ip_ver: ip version
    :param vm_iface: interface of vm
    :param firewalld: firewalld service instance
    :param params: test params
    :param test: test instance
    """
    test_file = params.get('test_file')
    rec_file = params.get('rec_file')
    cmd_create_file = params.get('cmd_create_file')
    force_dhcp = True if ip_ver == 'ipv4' else False
    vm_default_gw = utils_net.get_default_gateway(session=session,
                                                  ip_ver=ip_ver,
                                                  force_dhcp=force_dhcp)
    addr = vm_default_gw if ip_ver == 'ipv4' \
        else f'[{vm_default_gw}%{vm_iface}]'
    firewalld.stop()
    port = passt.get_free_port()
    cmd_listen = eval(params.get('cmd_listen'))
    cmd_transfer = eval(params.get('cmd_transfer'))
    session.cmd(cmd_create_file)
    md5_output = session.cmd_output(f'md5sum {test_file}')
    LOG.debug(md5_output)
    md5 = md5_output.split()[0]
    LOG.debug(f'MD5 of sent file: {md5}')
    host_session = aexpect.ShellSession('su')
    host_session.sendline(cmd_listen)
    session.cmd(cmd_transfer)
    host_session.close()
    if not os.path.exists(rec_file):
        test.fail(f'HOST did not recieve {rec_file}')
    host_file_md5 = process.run(
        f'md5sum {rec_file}', shell=True).stdout_text.split()[0]
    LOG.debug(f'MD5 of recieved file: {host_file_md5}')
    try:
        if md5 == host_file_md5:
            LOG.debug('MD5 check PASS')
        else:
            test.fail('MD5 of files from host and vm NOT match')
    finally:
        os.remove(rec_file)


def run(test, params, env):
    """
    Test file transfer between host and vm with passt backend interface
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

    iface_attrs = eval(params.get('iface_attrs'))
    params['socket_dir'] = socket_dir = eval(params.get('socket_dir'))
    params['proc_checks'] = proc_checks = eval(params.get('proc_checks', '{}'))
    vm_iface = params.get('vm_iface', 'eno1')
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state="UP")[0]
    log_file = f'/run/user/{user_id}/passt.log'
    iface_attrs['backend']['logFile'] = log_file
    iface_attrs['source']['dev'] = host_iface
    direction = params.get('direction')
    ip_ver = params.get('ip_ver')

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
        LOG.debug(virsh_ins.dumpxml(vm_name).stdout_text)

        session = vm.wait_for_serial_login(timeout=60)
        passt.check_default_gw(session)

        prot = 'TCP' if ip_ver == 'ipv4' else 'TCP6'
        if direction == 'host_to_vm':
            transfer_host_to_vm(session, prot, ip_ver, params, test)
        if direction == 'vm_to_host':
            transfer_vm_to_host(session, prot, ip_ver, vm_iface, firewalld,
                                params, test)
    finally:
        firewalld.start()
        vm.destroy()
        bkxml.sync(virsh_instance=virsh_ins)
        if root:
            shutil.rmtree(log_dir)
        else:
            del virsh_ins
        utils_selinux.set_status(selinux_status)
