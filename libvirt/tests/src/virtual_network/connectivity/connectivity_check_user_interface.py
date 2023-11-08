import logging
import re

import aexpect
from virttest import remote
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_unprivileged
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.virtual_network import network_base
from provider.virtual_network import passt

LOG = logging.getLogger('avocado.' + __name__)


def check_val(expect, actual, info, test):
    """
    Check whether actual value meets expectation

    :param expect: expect value
    :param actual: actual value
    :param info: output info if not matched
    :param test: test instance
    """
    if expect is None:
        return
    match = False
    if '+' in expect:
        match = re.search(expect, actual)
    elif 'nameserver' in info:
        match = expect in actual
    else:
        match = expect == actual
    if match:
        LOG.debug(f'{info} match. Expect {expect}, got {actual}')
    else:
        test.fail(f'{info} not match. Expect {expect}, but got {actual}')


def run(test, params, env):
    """
    Connectivity check of user type interface
    """
    expect_error = 'yes' == params.get('expect_error', 'no')
    err_msg = params.get('err_msg')
    virsh_uri = params.get('virsh_uri')
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
        virsh_ins = virsh.VirshPersistent(uri=virsh_uri)
        host_session = aexpect.ShellSession('su')
        remote.VMManager.set_ssh_auth(host_session, 'localhost', test_user,
                                      test_passwd)
        host_session.close()

    host_ip = utils_net.get_host_ip_address(ip_ver='ipv4')
    params['host_ip_v6'] = host_ip_v6 = utils_net.get_host_ip_address(
        ip_ver='ipv6')
    iface_attrs = eval(params.get('iface_attrs'))
    outside_ip = params.get('outside_ip')
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state='UP')[0]

    ipv4_addr = params.get('ipv4_addr')
    ipv4_prefix = params.get('ipv4_prefix')
    ipv6_addr = params.get('ipv6_addr')
    ipv6_prefix = params.get('ipipv6_prefixv4_addr')
    ipv4_default_gw = params.get('ipv4_default_gw')
    nameserver = params.get('nameserver')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name,
                                                   virsh_instance=virsh_ins)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        vmxml.sync(virsh_instance=virsh_ins)
        iface = libvirt_vmxml.create_vm_device_by_type(
            'interface', iface_attrs)
        vmxml.add_device(iface)
        define_result = virsh.define(vmxml.xml, debug=True, uri=virsh_uri)
        libvirt.check_exit_status(define_result, expect_error)

        if expect_error:
            libvirt.check_result(define_result, expected_fails=err_msg)
            return

        LOG.debug(virsh_ins.dumpxml(vm_name).stdout_text)
        vm.start()

        session = vm.wait_for_serial_login(timeout=60)
        LOG.debug(session.cmd_output('ip addr'))
        vm_iface = interface_base.get_vm_iface(session)
        vm_ipv4, vm_ipv4_pfx = passt.get_iface_ip_and_prefix(vm_iface, session)
        LOG.debug(f'vm ipv4 address and prefix: {vm_ipv4} {vm_ipv4_pfx}')
        check_val(ipv4_addr, vm_ipv4, 'vm ipv4', test)
        check_val(ipv4_prefix, str(vm_ipv4_pfx), 'vm ipv4 prefix', test)

        if ipv6_addr:
            vm_ipv6, vm_ipv6_pfx = passt.get_iface_ip_and_prefix(
                vm_iface, session, ip_ver='ipv6')[0]
            LOG.debug(f'vm ipv6 address and prefix: {vm_ipv6} {vm_ipv6_pfx}')
            check_val(ipv6_addr, vm_ipv6, 'vm ipv6', test)
            check_val(ipv6_prefix, str(vm_ipv6_pfx), 'vm ipv6 prefix', test)

        default_gw_v4 = utils_net.get_default_gateway(
            session=session, ip_ver='ipv4', force_dhcp=True)
        LOG.debug(f'vm default gateway ipv4: {default_gw_v4}')
        check_val(ipv4_default_gw, default_gw_v4, 'default ipv4 gateway', test)
        vm_nameserver = utils_net.get_guest_nameserver(session)
        check_val(nameserver, vm_nameserver, 'vm nameserver', test)

        ips = {
            'outside_ip': outside_ip,
            'host_public_ip': host_ip,
        }
        network_base.ping_check(params, ips, session, force_ipv4=True)
        session.close()
        vm.destroy()
    finally:
        bkxml.sync(virsh_instance=virsh_ins)
        if not root:
            del virsh_ins
