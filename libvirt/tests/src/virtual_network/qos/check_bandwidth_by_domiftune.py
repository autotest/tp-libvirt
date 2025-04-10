import logging

from avocado.utils import process
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check and tune the bandwidth of a interface via domiftune

    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    br_type = params.get('br_type', '')
    rand_id = utils_misc.generate_random_string(3)
    br_name = br_type + '_' + rand_id
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    net_attrs = eval(params.get('net_attrs', '{}'))
    update_bw = eval(params.get('update_bw', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    net_xml = NetworkXML.new_from_net_dumpxml('default')
    bk_net_xml = net_xml.copy()

    try:
        if br_type == 'linux_br':
            utils_net.create_linux_bridge_tmux(br_name, host_iface)
            process.run(f'ip l show type bridge {br_name}', shell=True)

        net_xml.setup_attrs(**net_attrs)
        net_xml.sync()
        LOG.debug(f'Network xml of default: {net_xml}')

        vmxml.del_device('interface', by_tag=True)

        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()
        session = vm.wait_for_serial_login()

        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        mac = iface.mac_address
        tap_device = libvirt.get_ifname_host(vm_name, mac)
        LOG.debug(f'tap device on host with mac {mac} is: {tap_device}.')

        domiftune_out = virsh.domiftune(
            vm_name, tap_device, debug=True).stdout_text
        domiftune_ori = libvirt_misc.convert_to_dict(
            domiftune_out, '(\S+)\s*:\s*(\d+)')
        if not all([v == '0' for v in domiftune_ori.values()]):
            test.fail('All value of domiftune output should be "0"')

        domiftune_args = {}
        bw_in = update_bw['inbound']
        inbound = f'{bw_in["average"]},{bw_in["peak"]},{bw_in["burst"]}'
        if 'floor' in bw_in:
            inbound = inbound + f',{bw_in["floor"]}'
        domiftune_args.update({'inbound': inbound})
        bw_out = update_bw['outbound']
        outbound = f'{bw_out["average"]},{bw_out["peak"]},{bw_out["burst"]}'
        domiftune_args.update({'outbound': outbound})
        virsh.domiftune(vm_name, tap_device, **domiftune_args, **VIRSH_ARGS)

        iface_update = network_base.get_iface_xml_inst(vm_name, 'after update')
        LOG.debug(iface_update.bandwidth)
        for key in ['inbound', 'outbound']:
            if iface_update.fetch_attrs()['bandwidth'][key] != update_bw[key]:
                test.fail(
                    f'Bandwidth of interface xml not correctly updated:\n'
                    f'{iface_update.fetch_attrs()["bandwidth"][key]} != '
                    f'{update_bw[key]}')
            LOG.debug(f'{key} bandwidth check in xml: PASS')

        # For direct interface type, "tc class show dev" will show the outbound
        # instead of the inbound setting, and "tc filter show dev" will show
        # the inbound instead of the outbound setting
        iface_type = iface_attrs['type_name']
        class_key = 'outbound' if iface_type == 'direct' else 'inbound'
        filter_key = 'inbound' if iface_type == 'direct' else 'outbound'

        if iface_type == 'network':
            source_br = iface.source['bridge']
            bw_floor = update_bw['inbound']['floor']
            update_bw['inbound'].pop('floor')
            if not utils_net.check_class_rules(
                    source_br, '1:3', {'floor': bw_floor}):
                test.fail('Class rule check (floor) after domiftune failed')
        if not utils_net.check_class_rules(
                tap_device, '1:1', update_bw[class_key]):
            test.fail(
                'Class rule check after domiftune failed. Please check log.')
        LOG.debug('Class rule check after domiftune: PASS')
        if not utils_net.check_filter_rules(
                tap_device, update_bw[filter_key]):
            test.fail(
                'Filter rule check after domiftune failed. Please check log.')
        LOG.debug('Filter rule check after domiftune: PASS')

        virsh.domiftune(vm_name, tap_device, inbound='0',
                        outbound='0', **VIRSH_ARGS)
        iface_del_bw = network_base.get_iface_xml_inst(
            vm_name, 'after delete bandwidth')

        if 'bandwidth' in iface_del_bw.fetch_attrs():
            test.fail('Bandwidth of interface xml not deleted')
        out_c = utils_net.check_class_rules(
            tap_device, "1:1", update_bw["inbound"], expect_none=True
        )
        out_f = utils_net.check_filter_rules(
            tap_device, update_bw["outbound"], expect_none=True
        )
        if not all([out_c, out_f]):
            test.fail(
                "There should not be output of tc class/filter "
                "command when bandwidth settings is deleted."
            )
        LOG.debug('Class and Filter rule check with "tc" '
                  'after delete bandwidth with domiftune: PASS')

    finally:
        if 'session' in locals():
            session.close()
        bkxml.sync()
        bk_net_xml.sync()
        if br_type == 'linux_br':
            utils_net.delete_linux_bridge_tmux(br_name, host_iface)
