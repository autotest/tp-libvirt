import logging

from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check the actual network throughput for direct type interface

    """
    vm_name = params.get('main_vm')
    vm2_name = params.get('vm2_name')
    vm = env.get_vm(vm_name)
    vm2 = env.get_vm(vm2_name)
    rand_id = utils_misc.generate_random_string(3)
    net_name = 'net_' + rand_id
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True, json=True)
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    iface_attrs_2 = eval(params.get('iface_attrs_2', '{}'))
    net_attrs = eval(params.get('net_attrs', '{}'))
    inbound = eval(params.get('inbound'))
    outbound = eval(params.get('outbound'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vmxml_2 = vm_xml.VMXML.new_from_inactive_dumpxml(vm2_name)
    bkxml_2 = vmxml_2.copy()
    firewalld = service.Factory.create_service("firewalld")

    try:

        if 'name' in net_attrs:
            libvirt_network.create_or_del_network(net_attrs)
            virsh.net_dumpxml(net_attrs['name'], **VIRSH_ARGS)

        mac = network_base.get_iface_xml_inst(
            vm_name, f'on VM:{vm_name}').mac_address
        mac_2 = network_base.get_iface_xml_inst(
            vm2_name, f'on VM:{vm2_name}').mac_address
        iface_attrs.update({'mac_address': mac})
        iface_attrs_2.update({'mac_address': mac_2})

        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vmxml_2.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml_2, 'interface', iface_attrs_2)
        LOG.debug(
            f'VMXML of {vm2_name}:\n{virsh.dumpxml(vm2_name).stdout_text}')

        vm.start()
        vm_sess = vm.wait_for_serial_login()

        vm2.start()
        vm_sess_2 = vm2.wait_for_serial_login()

        network_base.get_iface_xml_inst(vm_name, f'on VM:{vm_name}')
        network_base.get_iface_xml_inst(vm2_name, f'on VM:{vm2_name}')

        tap_device = libvirt.get_ifname_host(vm_name, mac)
        LOG.debug(f'tap device on host with mac {mac} is: {tap_device}.')

        tc_args = [tap_device, '1:1', iface_attrs['bandwidth']['outbound']]
        filter_bw = iface_attrs['bandwidth']['inbound']

        if not utils_net.check_class_rules(*tc_args):
            test.fail('Class rule check failed. Please check log.')
        LOG.debug('Class rule check: PASS')

        if not utils_net.check_filter_rules(tc_args[0], filter_bw):
            test.fail('Filter rule check failed. Please check log.')
        LOG.debug('Filter rule check: PASS')

        if not utils_package.package_install('netperf', session=vm_sess):
            test.error(f'Failed to install netperf on VM:{vm_name}.')
        if not utils_package.package_install('netperf', session=vm_sess_2):
            test.error(f'Failed to install netperf on VM:{vm2_name}.')

        vm_sess.close()
        vm_sess = vm.wait_for_serial_login()
        vm_sess_2.close()
        vm_sess_2 = vm2.wait_for_serial_login()

        vm_ip = network_base.get_vm_ip(vm_sess, mac)
        vm_ip_2 = network_base.get_vm_ip(vm_sess_2, mac_2)
        LOG.debug(f'Vm {vm_name} ip address: {vm_ip}\n'
                  f'Vm {vm2_name} ip address: {vm_ip_2}')

        firewalld.stop()
        vm_sess.cmd('systemctl stop firewalld')
        vm_sess_2.cmd('systemctl stop firewalld')

        network_base.check_throughput(
            vm_sess.cmd, vm_sess_2.cmd,
            vm_ip, outbound["average"], 'outbound'
        )

        network_base.check_throughput(
            vm_sess_2.cmd,
            vm_sess.cmd,  vm_ip_2, inbound["average"], 'inbound'
        )
        vm_sess.close()
        vm_sess_2.close()

    finally:
        bkxml.sync()
        bkxml_2.sync()
        if 'name' in net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
        firewalld.start()
