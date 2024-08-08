import logging

from avocado.utils import process
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def domiftune_to_dict(output):
    """
    Convert domiftune output to dict

    :param output: domiftune output
    :return: converted dict
    """
    tmp_dict = libvirt_misc.convert_to_dict(output, '(\S+)\s*:\s*(\d+)')
    final = {}
    final['inbound'] = {k.split('.')[-1]: v for k, v in tmp_dict.items()
                        if k.startswith('inbound')}
    final['outbound'] = {k.split('.')[-1]: v for k, v in tmp_dict.items()
                         if k.startswith('outbound')}
    return final


def run(test, params, env):
    """
    Test live update interface bandwidth setting by update-device or domiftune
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
    extra_attrs = eval(params.get('extra_attrs', '{}'))
    net_attrs = eval(params.get('net_attrs', '{}'))

    scope = eval(params.get('scope', '[]'))
    method = params.get('method', '')
    operation = params.get('operation', '')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if br_type == 'linux_br':
            utils_net.create_linux_bridge_tmux(br_name, host_iface)
            process.run(f'ip l show type bridge {br_name}', shell=True)
        elif br_type == 'ovs_br':
            utils_net.create_ovs_bridge(br_name)

        vmxml.del_device('interface', by_tag=True)

        # Update iface_attrs with certain bandwidth settings
        extra_attrs = {'bandwidth': {k: extra_attrs['bandwidth'][k]
                                     for k in scope}
                       } if extra_attrs else extra_attrs
        iface_attrs.update(extra_attrs)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        # For direct interface type, "tc class show dev" will show the outbound
        # instead of the inbound setting, and "tc filter show dev" will show
        # the inbound instead of the outbound setting
        iface_type = iface_attrs['type_name']
        class_key = 'outbound' if iface_type == 'direct' else 'inbound'
        filter_key = 'inbound' if iface_type == 'direct' else 'outbound'

        vm.start()
        session = vm.wait_for_serial_login()

        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        mac = iface.mac_address
        tap_device = libvirt.get_ifname_host(vm_name, mac)
        LOG.debug(f'tap device on host with mac {mac} is: {tap_device}.')

        if 'bandwidth' in iface_attrs:
            if class_key in scope and not utils_net.check_class_rules(
                    tap_device, '1:1', iface_attrs['bandwidth'][class_key]):
                test.fail(
                    'Class rule check before update failed. Please check log.')
            if filter_key in scope and not utils_net.check_filter_rules(
                    tap_device, iface_attrs['bandwidth'][filter_key]):
                test.fail(
                    'Filter rule check before update failed. Please check log.')

        # Remove out-of-scope settings
        update_attrs = eval(params.get('update_attrs', '{}'))
        update_attrs['bandwidth'] = {k: update_attrs['bandwidth'][k]
                                     for k in scope}
        LOG.debug(f'Update iface with attrs: {update_attrs}')
        if method == 'update-device':
            if operation == 'delete':
                iface.del_bandwidth()
            else:
                iface.setup_attrs(**update_attrs)
            LOG.debug(f'Update iface with xml:\n{iface}')
            virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)

        elif method == 'domiftune':
            domiftune_args = {}
            if 'inbound' in scope:
                bw_in = update_attrs['bandwidth']['inbound']
                inbound = f'{bw_in["average"]},{bw_in["peak"]},{bw_in["burst"]}'
                domiftune_args.update({'inbound': inbound})
            if 'outbound' in scope:
                bw_out = update_attrs['bandwidth']['outbound']
                outbound = f'{bw_out["average"]},{bw_out["peak"]},{bw_out["burst"]}'
                domiftune_args.update({'outbound': outbound})
            virsh.domiftune(vm_name, tap_device, **
                            domiftune_args, **VIRSH_ARGS)
        else:
            test.error(f'Unsupported method: {method}.')

        iface_update = network_base.get_iface_xml_inst(vm_name, 'after update')
        if operation == 'delete':
            if 'bandwidth' in iface_update.fetch_attrs():
                test.fail('Bandwidth of interface xml not deleted')
        else:
            LOG.debug(iface_update.bandwidth)
            for key in scope:
                if iface_update.fetch_attrs()['bandwidth'][key] != update_attrs['bandwidth'][key]:
                    test.fail(
                        'Bandwidth of interface xml not correctly updated')

        if operation == 'delete':
            out_c = process.run(f'tc class show dev {tap_device}'
                                ).stdout_text.strip()
            out_f = process.run(f'tc filter show dev {tap_device} parent ffff:'
                                ).stdout_text.strip()
            if any([out_c, out_f]):
                test.fail('There should not be output of tc class/filter '
                          'command when bandwidth settings is deleted.')
        else:
            if class_key in scope and not utils_net.check_class_rules(
                    tap_device, '1:1', update_attrs['bandwidth'][class_key]):
                test.fail(
                    'Class rule check after update failed. Please check log.')
            if filter_key in scope and not utils_net.check_filter_rules(
                    tap_device, update_attrs['bandwidth'][filter_key]):
                test.fail(
                    'Filter rule check after update failed. Please check log.')

        domiftune_output = virsh.domiftune(vm_name, tap_device,
                                           **VIRSH_ARGS).stdout_text
        domiftune_dict = domiftune_to_dict(domiftune_output)
        LOG.debug(f'domiftune to dict: {domiftune_dict}')
        for key in scope:
            if not update_attrs['bandwidth'][key].items() <= \
                    domiftune_dict[key].items():
                test.fail(f'domiftune {key} check fail.')

    finally:
        if 'session' in locals():
            session.close()
        bkxml.sync()
        if br_type == 'linux_br':
            utils_net.delete_linux_bridge_tmux(br_name, host_iface)
        if br_type == 'ovs_br':
            utils_net.delete_ovs_bridge(br_name)
