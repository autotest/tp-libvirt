import logging

from avocado.utils import process
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.staging import service
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check the actual network throughput
    per bandwidth settings in network/interface

    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    br_type = params.get('br_type', '')
    rand_id = utils_misc.generate_random_string(3)
    br_name = br_type + '_' + rand_id
    net_name = 'net_' + rand_id
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state='UP')[0]
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    net_attrs = eval(params.get('net_attrs', '{}'))
    inbound = eval(params.get('inbound'))
    outbound = eval(params.get('outbound'))
    throuput_bw = params.get('throuput_bw')
    expect_msg = params.get('expect_msg')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    net_xml = NetworkXML.new_from_net_dumpxml('default')
    bk_net_xml = net_xml.copy()
    firewalld = service.Factory.create_service("firewalld")
    iface_type = params.get("iface_type")

    try:
        if br_type == 'linux_br':
            utils_net.create_linux_bridge_tmux(br_name, host_iface)
            process.run(f'ip l show type bridge {br_name}', shell=True)
        elif br_type == 'ovs_br':
            utils_net.create_ovs_bridge(br_name)

        if 'name' in net_attrs:
            libvirt_network.create_or_del_network(net_attrs)
        else:
            net_xml.setup_attrs(**net_attrs)
            net_xml.sync()
            LOG.debug(f'Network xml of default: {net_xml}')

        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()
        vm_sess = vm.wait_for_serial_login()
        if expect_msg:
            libvirt.check_logfile(expect_msg,
                                  params.get("libvirtd_debug_file"))

        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        mac = iface.mac_address
        tap_device = libvirt.get_ifname_host(vm_name, mac)
        LOG.debug(f'tap device on host with mac {mac} is: {tap_device}.')
        source_br = iface.source['bridge']
        LOG.debug(f'source bridge of interface is: {source_br}.')

        if 'bandwidth' in iface_attrs:
            tc_args = [tap_device, '1:1', iface_attrs['bandwidth']['inbound']]
            filter_bw = iface_attrs['bandwidth']['outbound']
        else:
            tc_args = [source_br, '1:2', net_attrs['bandwidth_inbound']]
            filter_bw = net_attrs['bandwidth_outbound']

        if not utils_net.check_class_rules(*tc_args):
            test.fail('Class rule check failed. Please check log.')
        LOG.debug('Class rule check: PASS')

        if not utils_net.check_filter_rules(tc_args[0], filter_bw):
            test.fail('Filter rule check failed. Please check log.')
        LOG.debug('Filter rule check: PASS')

        if not utils_package.package_install('netperf', session=vm_sess):
            test.error('Failed to install netperf on VM.')

        vm_sess.close()
        vm_sess = vm.wait_for_serial_login()

        host_ip = utils_net.get_linux_iface_info(
            iface=source_br)['addr_info'][0]['local']
        vm_ip = network_base.get_vm_ip(vm_sess, mac)
        LOG.debug(f'Host ip address with br {source_br}: {host_ip}\n'
                  f'Vm ip address: {vm_ip}')

        firewalld.stop()
        vm_sess.cmd('systemctl stop firewalld')

        network_base.check_throughput(
            vm_sess.cmd, lambda x: process.run(x).stdout_text,
            vm_ip, throuput_bw if throuput_bw else inbound["average"], 'inbound'
        )

        network_base.check_throughput(
            lambda x: process.run(x).stdout_text,
            vm_sess.cmd,  host_ip, outbound["average"], 'outbound'
        )
        vm_sess.close()
        vm.destroy()
        if iface_type == "bridge" and br_type == "ovs_br":
            ovs_list_cmd = 'ovs-vsctl list qos'
            ovs_list_output = process.run(ovs_list_cmd).stdout_text
            if ovs_list_output:
                test.fail(f'There should be no output of command {ovs_list_cmd}.'
                          f'But we got {ovs_list_output}')

    finally:
        bkxml.sync()
        bk_net_xml.sync()
        if 'name' in net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
        if br_type == 'linux_br':
            utils_net.delete_linux_bridge_tmux(br_name, host_iface)
        if br_type == 'ovs_br':
            utils_net.delete_ovs_bridge(br_name)
        firewalld.start()
