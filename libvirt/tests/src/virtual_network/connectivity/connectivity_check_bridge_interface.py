import logging
import re

from avocado.utils import process

from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import nwfilter_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def parse_attrs(target, params):
    """
    Parse params to get target attrs

    :param target: prefix of params key
    :param params: test params
    :return: dict type attrs
    """
    attrs = {k.replace(target + '_', ''): int(v) if v.isdigit()
             else eval(v) if v[0] in '{[' else v
             for k, v in params.items() if v and k.startswith(target)}
    LOG.debug(f'{target}: {attrs}')
    return attrs


def run(test, params, env):
    """
    Check connectivity for bridge type interface
    """
    vm_name = params.get('main_vm')
    vms = params.get('vms').split()
    vm, ep_vm = (env.get_vm(vm_i) for vm_i in vms)
    scenario = params.get('scenario')
    outside_ip = params.get('outside_ip')
    rand_id = utils_misc.generate_random_string(3)
    bridge_type = params.get('bridge_type', '')
    bridge_name = bridge_type + '_' + rand_id
    iface_attrs = parse_attrs('iface_attrs', params)
    iface_attrs['source']['bridge'] = bridge_name
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    nwfilter_attrs = parse_attrs('nwfilter_attrs', params)
    iface_in_vm = params.get('iface_in_vm')
    iface_in_vm = iface_in_vm if iface_in_vm else 'eno'

    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]

    bkxmls = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

    try:
        if bridge_type == 'linux_br':
            utils_net.create_linux_bridge_tmux(bridge_name, host_iface)
            process.run(f'ip l show type bridge {bridge_name}', shell=True)
        elif bridge_type == 'ovs_br':
            utils_net.create_ovs_bridge(bridge_name)

        if nwfilter_attrs:
            nwf = nwfilter_xml.NwfilterXML()
            nwf.setup_attrs(**nwfilter_attrs)
            virsh.nwfilter_define(nwf.xml, **VIRSH_ARGS)
            LOG.debug('nwfilter xml:\n' + virsh.nwfilter_dumpxml(
                nwfilter_attrs['filter_name']).stdout_text)

        vmxml, ep_vmxml = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

        if vm_attrs:
            [vmxml_i.setup_attrs(**vm_attrs) for vmxml_i in [vmxml, ep_vmxml]]

        [vmxml_i.del_device('interface', by_tag=True) for vmxml_i in
         [vmxml, ep_vmxml]]
        [libvirt_vmxml.modify_vm_device(vmxml_i, 'interface', iface_attrs)
         for vmxml_i in [vmxml, ep_vmxml]]

        [LOG.debug(f'VMXML of {vm_x}:\n{virsh.dumpxml(vm_x).stdout_text}')
         for vm_x in vms]

        [vm_i.start() for vm_i in [vm, ep_vm]]
        session, ep_session = (vm_inst.wait_for_serial_login()
                               for vm_inst in [vm, ep_vm])
        mac, ep_mac = list(map(vm_xml.VMXML.get_first_mac_by_name, vms))

        ips_v4 = network_base.get_test_ips(session, mac, ep_session, ep_mac,
                                           net_name=None,
                                           ip_ver='ipv4')
        ips_v4['outside_ip'] = outside_ip

        network_base.ping_check(params, ips_v4, session, force_ipv4=True)

        if 'acpi' in iface_attrs:
            iface_in_vm += iface_attrs['acpi']['index']
            check_cmd = 'ip l'
            output = session.cmd_output(check_cmd)
            if iface_in_vm not in output:
                test.fail(f'Expect interface name in vm is {iface_in_vm}, '
                          f'actual output is:\n{output}')
            LOG.debug(f'Output of cmd {check_cmd}:\n{output}')

        if 'mtu' in iface_attrs:
            mtu_size = iface_attrs["mtu"]["size"]
            check_cmd = 'ip l show ' + iface_in_vm
            output = session.cmd_output(check_cmd)
            if f'mtu {mtu_size}' not in output:
                test.fail(f'Expect mtu size: {mtu_size}, '
                          f'actual output: {output}')
            LOG.debug(f'Output of cmd {check_cmd}:\n{output}')

        if scenario == 'multiqueue':
            def _check_channel_info(expect_max, expect_cur):
                c_max, c_cur = utils_net.get_channel_info(session, iface_in_vm)
                LOG.debug(f'Channel info maximums:\n{c_max}\nCurrent:\n{c_cur}')
                if c_max['Combined'] != expect_max:
                    test.fail(f'Expect channel combined: {expect_max}, '
                              f'actual: {c_max["Combined"]}')
                if c_cur['Combined'] != expect_cur:
                    test.fail(f'Expect channel combined: {expect_max}, '
                              f'actual: {c_max["Combined"]}')

            expect_max = iface_attrs['driver']['driver_attr']['queues']
            expect_cur = expect_max
            _check_channel_info(expect_max, expect_cur)

            expect_cur = str(int(expect_max) - 1)
            set_cmd = f'ethtool -L {iface_in_vm} combined {expect_cur}'
            session.cmd(set_cmd)
            _check_channel_info(expect_max, expect_cur)

        if 'coalesce' in iface_attrs:
            output = virsh.domiflist(vm_name, **VIRSH_ARGS).stdout_text.strip()
            iflist_info = libvirt_misc.convert_to_dict(output.splitlines()[-1],
                                                       pattern=r'(\S+) +(.*)')
            vnet_name = list(iflist_info.keys())[0]
            LOG.debug(f'vnet_name: {vnet_name}')
            check_cmd = 'ethtool -c ' + vnet_name
            output = process.run(check_cmd, shell=True).stdout_text
            expect_str = 'rx-frames:\s+' + str(iface_attrs['coalesce']['max'])
            if not re.search(expect_str, output):
                test.fail(f'Expect {expect_str}, actual output: {output}')

        if 'alias' in iface_attrs:
            set_alias = iface_attrs['alias']['name']
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            iface_after = vmxml.get_devices('interface')[0]
            actual_alias = iface_after.alias['name']
            if set_alias != actual_alias:
                test.fail(f'Expect interface alias: {set_alias}, '
                          f'actual output: {actual_alias}')

    finally:
        [backup_xml.sync() for backup_xml in bkxmls]
        if bridge_type == 'linux_br':
            utils_net.delete_linux_bridge_tmux(bridge_name, host_iface)
        if bridge_type == 'ovs_br':
            utils_net.delete_ovs_bridge(bridge_name)
        if nwfilter_attrs:
            virsh.nwfilter_undefine(nwfilter_attrs['filter_name'])
