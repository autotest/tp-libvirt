import logging

from avocado.utils import process
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Update interface portgroup for nat and bridge network(Qos)

    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    rand_id = utils_misc.generate_random_string(3)
    net_type = params.get('net_type', '')
    br_name = net_type + '_' + rand_id
    net_name = params.get('net_type') + '_' + rand_id
    net_attrs = eval(params.get('net_attrs', '{}'))
    pg_a = eval(params.get('pg_a', '{}'))
    pg_b = eval(params.get('pg_b', '{}'))
    update_attrs = eval(params.get('update_attrs', '{}'))

    net_name = 'default' if 'name' not in net_attrs else net_name
    iface_attrs = eval(params.get('iface_attrs', '{}'))

    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state="UP")[0]

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    net_xml = NetworkXML.new_from_net_dumpxml('default')
    bk_net_xml = net_xml.copy()

    try:
        LOG.info('Create bridge for test.')
        if net_type == 'linux_br':
            utils_net.create_linux_bridge_tmux(br_name, host_iface)
            process.run(f'ip l show type bridge {br_name}', shell=True)
        elif net_type == 'ovs_br':
            utils_net.create_ovs_bridge(br_name)

        if 'name' in net_attrs:
            libvirt_network.create_or_del_network(net_attrs)
            virsh.net_dumpxml(net_attrs['name'], **VIRSH_ARGS)
        else:
            net_xml.setup_attrs(**net_attrs)
            net_xml.sync()
            LOG.debug(f'Network xml of default: {net_xml}')

        vmxml.del_device('interface', by_tag=True)
        iface = libvirt_vmxml.create_vm_device_by_type(
            'interface', iface_attrs)
        libvirt.add_vm_device(vmxml, iface)
        LOG.debug(f'VMXMLof {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        iface = network_base.get_iface_xml_inst(vm_name, '1st on vm')
        mac = iface.mac_address

        vm.start()
        network_base.get_iface_xml_inst(vm_name, 'on vm after vm start')
        tap_device = libvirt.get_ifname_host(vm_name, mac)
        LOG.debug(f'tap device on host with mac {mac} is: {tap_device}.')

        if not utils_net.check_class_rules(
                tap_device, '1:1', pg_a['bandwidth_inbound']):
            test.fail('class rule check failed')
        if not utils_net.check_filter_rules(
                tap_device, pg_a['bandwidth_outbound']):
            test.fail('filter rule check failed')

        # Update device
        update_attrs = {'source': {**iface_attrs['source'], **update_attrs}}
        LOG.debug(f'Update iface with attrs: {update_attrs}')
        iface.setup_attrs(**update_attrs)
        iface.xmltreefile.write()

        LOG.debug(f'Update iface with xml: {iface}')
        virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)

        # Check updated iface xml
        iface_update = network_base.get_iface_xml_inst(vm_name, 'after update')
        if iface_update.source.get(
                'portgroup') != update_attrs['source']['portgroup']:
            test.fail('Update iface xml portgroup failed')

        # Check rules
        if not utils_net.check_class_rules(
                tap_device, '1:1', pg_b['bandwidth_inbound']):
            test.fail('class rule check failed-')
        if not utils_net.check_filter_rules(
                tap_device, pg_b['bandwidth_outbound']):
            test.fail('filter rule check failed-')

    finally:
        bkxml.sync()
        bk_net_xml.sync()
        if 'name' in net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
        if net_type == 'linux_br':
            utils_net.delete_linux_bridge_tmux(br_name, host_iface)
        if net_type == 'ovs_br':
            utils_net.delete_ovs_bridge(br_name)
