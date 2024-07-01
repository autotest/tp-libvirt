import logging

import aexpect
from virttest import remote
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_unprivileged
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test attach interface with user type
    """
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

    iface_attrs = eval(params.get('iface_attrs', '{}'))
    expect_ipv4 = params.get('expect_ipv4')
    expect_ipv6 = params.get('expect_ipv6')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
        vm_name, virsh_instance=virsh_ins)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        vmxml.sync(virsh_instance=virsh_ins)
        mac = iface_attrs['mac_address']
        LOG.debug(
            f'VMXML of {vm_name}:\n{virsh_ins.dumpxml(vm_name).stdout_text}')

        vm.start()
        vm.wait_for_serial_login().close()

        iface = libvirt_vmxml.create_vm_device_by_type(
            'interface', iface_attrs)
        LOG.debug(f'Interface to attach:\n{iface}')

        virsh_ins.attach_device(vm_name, iface.xml, **VIRSH_ARGS)
        LOG.debug(virsh_ins.dumpxml(vm_name).stdout_text)

        # Check attached xml
        iface_at = network_base.get_iface_xml_inst(
            vm_name, 'after attached on vm', virsh_ins=virsh_ins)
        if iface_at.mac_address != iface.mac_address:
            test.fail(f'Mac address of attached iface xml does not match with '
                      f'original xml, attach might failed')

        # log in the guest, and check the network function, it works well,
        # ip and ping
        session = vm.wait_for_serial_login()
        vm_iface_info = utils_net.get_linux_iface_info(
            mac=mac, session=session)
        LOG.debug(f'Interface in vm: {vm_iface_info}')
        for addr in vm_iface_info['addr_info']:
            if addr['scope'] != 'global':
                continue
            if addr['family'] == 'inet':
                if addr['local'] != expect_ipv4:
                    test.fail(f'Wrong ipv4 addr: {addr["local"]}')
            if addr['family'] == 'inet6':
                if not addr['local'].startswith(expect_ipv6):
                    test.fail(f'Wrong ipv6 addr {addr["local"]}')

        LOG.info('Login vm to check whether the network works well via ping.')
        ips = {'outside_ip': params.get('outside_ip')}
        network_base.ping_check(params, ips, session, force_ipv4=True)

        # Detach iface
        virsh_ins.detach_device(
            vm_name, iface.xml, wait_for_event=True, **VIRSH_ARGS)

        # Check iface in vm disappeared
        vm_iface_info_unplug = utils_net.get_linux_iface_info(
            mac=mac, session=session)
        LOG.debug(vm_iface_info_unplug)
        if vm_iface_info_unplug is not None:
            test.fail(f'iface should be removed, should not found '
                      f'{vm_iface_info_unplug} in vm')

        # Check xml disappeared
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(
            vm_name, virsh_instance=virsh_ins)
        iface_list = vmxml.devices.by_device_tag('interface')
        if len(iface_list):
            test.fail('Found interface xml on vm which should be detached:'
                      f'{iface_list}')
    finally:
        bkxml.sync(virsh_instance=virsh_ins)
