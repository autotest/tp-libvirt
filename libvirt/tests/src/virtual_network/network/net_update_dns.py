import logging
import re

from avocado.utils import process
from virttest import libvirt_version
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import DNSXML
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.utils_test import libvirt

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + 'net_update_dns')


def check_pid_not_change(test, target_pids):
    LOG.debug(f'Pids before operation: {target_pids}')
    pids = process.run(
        'pidof virtnetworkd; pidof virtqemud', shell=True).stdout_text
    if set(pids.split()) != set(target_pids.split()):
        test.fail('Pids changed after operation')


def run(test, params, env):
    """
    Check the actual network throughput for direct type interface
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    net_name = 'default'
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    operation = params.get('operation')
    dns_ele = params.get('dns_ele')
    update_xml = params.get('update_xml')
    update_attrs = eval(params.get('update_attrs', '{}'))
    conf_val = params.get('conf_val')
    post_check = params.get('post_check')
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    dns_attrs = eval(params.get('dns_attrs'))
    default_net = NetworkXML.new_from_net_dumpxml('default')
    bk_net = default_net.copy()

    try:
        libvirt_version.is_libvirt_feature_supported(params)

        default_net.setup_attrs(dns=dns_attrs)
        default_net.sync()

        virsh.net_dumpxml(net_name, **VIRSH_ARGS)

        if dns_ele == 'dns-txt':
            cmd_ori_val = f'cat /var/lib/libvirt/dnsmasq/{net_name}.conf  | grep txt'
        if dns_ele == 'dns-host':
            cmd_ori_val = f'cat /var/lib/libvirt/dnsmasq/{net_name}.addnhosts'
            host_xml = DNSXML.HostXML()
            host_xml.setup_attrs(**update_attrs)
            update_xml = host_xml.xml

        ori_val = process.run(cmd_ori_val, shell=True).stdout_text
        pids = process.run(
            'pidof virtnetworkd; pidof virtqemud', shell=True).stdout_text

        up_result = virsh.net_update(
            net_name, operation, dns_ele, update_xml, debug=True)
        libvirt.check_exit_status(up_result, status_error)
        if err_msg:
            libvirt.check_result(up_result, err_msg)

        virsh.net_dumpxml(net_name, **VIRSH_ARGS)

        if not status_error:
            new_net = NetworkXML.new_from_net_dumpxml(net_name)
            new_dns = new_net.dns.fetch_attrs()
            dns_key = dns_ele.split('-')[-1]
            for k in update_attrs.keys():
                msg = f'expect key [{k}] has value [{update_attrs[k]}], actually got [{new_dns[dns_key].get(k)}]'
                LOG.debug(msg)
                if new_dns[dns_key].get(k) != update_attrs[k]:
                    test.fail(f'Compare updated xml failed, {msg}')

            new_val = process.run(cmd_ori_val, shell=True).stdout_text
            LOG.debug(f'Expect to find {conf_val} in conf file')
            if not re.search(conf_val, new_val):
                test.fail(f'Failed to find expected {conf_val} in conf file')
        if post_check:
            if post_check == 'check_pid_not_change':
                check_pid_not_change(test, pids)

    finally:
        bk_net.sync()
        bkxml.sync()
