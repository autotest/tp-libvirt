import logging

from avocado.utils import process
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.staging import service
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base, netperf_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check the actual network throughput
    per bandwidth settings in network/interface
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    rand_id = utils_misc.generate_random_string(3)
    net_name = 'net_' + rand_id
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    net_attrs = eval(params.get('net_attrs', '{}'))
    inbound = eval(params.get('inbound'))
    outbound = eval(params.get('outbound'))
    throuput_bw = params.get('throuput_bw')
    expect_msg = params.get('expect_msg')
    iface_type = params.get("iface_type", "bridge")
    bridge_name = params.get('netdst', '')

    # Original case design: 'virbr0' (NAT) and 'bridge' variants are mutually exclusive.
    # Prevent pollution of the core libvirt bridge with incompatible bridge variants.
    if bridge_name == 'virbr0':
        net_fwm = params.get('net_fwm', '')
        if iface_type == 'bridge' or (iface_type == 'network' and net_fwm == 'bridge'):
            test.cancel("Original logic: 'virbr0' only supports 'nat' variant. Skipping bridge variant.")

    is_ovs_bridge = False
    if bridge_name:
        try:
            is_ovs_bridge = not isinstance(utils_net.find_bridge_manager(bridge_name), utils_net.Bridge)
        except process.CmdError:
            pass

    # Dynamic attribute configuration for OVS
    if iface_type == 'bridge':
        iface_attrs['source'] = {'bridge': bridge_name}
        network_base.setup_ovs_bridge_attrs(params, iface_attrs)
        if is_ovs_bridge:
            expect_msg = r"warning.*Setting different .peak. value than .average. for QoS for OVS interface.*might have unexpected results"
            throuput_bw = 1024
            libvirtd_debug_file = params.get("libvirtd_debug_file", "/var/log/libvirt/libvirtd.log")
            params["libvirtd_debug_file"] = libvirtd_debug_file
    elif iface_type == 'network' and params.get('net_fwm') == 'bridge':
        net_attrs['bridge'] = {'name': bridge_name}
        if is_ovs_bridge:
            net_attrs['virtualport_type'] = 'openvswitch'

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    net_xml = NetworkXML.new_from_net_dumpxml('default')
    bk_net_xml = net_xml.copy()
    firewalld = service.Factory.create_service("firewalld")
    fw_was_running = firewalld.status()

    try:
        # 1. Setup Network or modify Interface QoS
        if 'name' in net_attrs:
            libvirt_network.create_or_del_network(net_attrs)
        else:
            net_xml.setup_attrs(**net_attrs)
            net_xml.sync()
            LOG.debug(f'Network xml of default: {net_xml}')

        orig_iface = vmxml.get_devices('interface')[0]
        iface_attrs['mac_address'] = orig_iface.mac_address

        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()

        login_timeout = params.get_numeric("login_timeout", 360)
        vm_sess = vm.wait_for_login(timeout=login_timeout)
        if expect_msg:
            libvirt.check_logfile(expect_msg, params.get("libvirtd_debug_file", "/var/log/libvirt/libvirtd.log"))

        # 3. Verify TC rules (Class and Filter)
        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        mac = iface.mac_address
        tap_device = libvirt.get_ifname_host(vm_name, mac)
        LOG.debug(f'tap device on host with mac {mac} is: {tap_device}.')
        source_br = iface.source.get('bridge')
        if source_br:
            LOG.debug(f'source bridge of interface is: {source_br}.')

        if 'bandwidth' in iface_attrs:
            tc_args = [tap_device, '1:1', iface_attrs['bandwidth']['inbound']]
            filter_bw = iface_attrs['bandwidth']['outbound']
        else:
            tc_args = [source_br, '1:2', net_attrs['bandwidth_inbound']]
            filter_bw = net_attrs['bandwidth_outbound']

        if not utils_net.check_class_rules(*tc_args):
            test.fail('Class rule check (Inbound) failed. Please check log.')
        LOG.debug('Class rule check: PASS')

        if not utils_net.check_filter_rules(tc_args[0], filter_bw):
            test.fail('Filter rule check (Outbound) failed. Please check log.')
        LOG.debug('Filter rule check: PASS')

        # 4. Prepare Netperf Environment
        firewalld.stop()
        disable_firewall = params.get("disable_firewall", "systemctl stop firewalld.service")
        vm_sess.cmd(disable_firewall)

        host_ip = utils_net.get_linux_iface_info(iface=source_br)['addr_info'][0]['local']
        vm_ip = network_base.get_vm_ip(vm_sess, mac)
        LOG.debug(f'Host ip address with br {source_br}: {host_ip}\n'
                  f'Vm ip address: {vm_ip}')

        # Compile netperf for both host and VM
        host_nserver, host_nperf = netperf_base.compile_netperf_pkg(params, env, "localhost")
        vm_nserver, vm_nperf = netperf_base.compile_netperf_pkg(params, env, vm_name)

        # 5. Throughput Validation
        test.log.info("TEST_STEP: Checking Inbound throughput (Host -> VM)")
        network_base.check_throughput(
            vm_sess.cmd, lambda x: process.run(x, shell=True).stdout_text,
            vm_ip, throuput_bw if throuput_bw else inbound["average"], 'inbound',
            netserver_cmd=vm_nserver, netperf_cmd=host_nperf
        )

        test.log.info("TEST_STEP: Checking Outbound throughput (VM -> Host)")
        network_base.check_throughput(
            lambda x: process.run(x, shell=True).stdout_text, vm_sess.cmd,
            host_ip, outbound["average"], 'outbound',
            netserver_cmd=host_nserver, netperf_cmd=vm_nperf
        )
        vm_sess.cmd(f"rm -rf {vm_nserver.split('/src/')[0]}")
        vm.destroy(gracefully=False)
        if iface_type == "bridge" and is_ovs_bridge:
            ovs_list_cmd = 'ovs-vsctl list qos'
            ovs_list_output = process.run(ovs_list_cmd).stdout_text
            if ovs_list_output:
                test.fail(f'There should be no output of command {ovs_list_cmd}.'
                          f'But we got {ovs_list_output}')

    finally:
        # Unified and safe cleanup logic
        if host_nserver:
            process.run(f"rm -rf {host_nserver.split('/src/')[0]}", shell=True)
        if vm_sess:
            vm_sess.close()

        bkxml.sync()
        bk_net_xml.sync()
        if 'name' in net_attrs:
            libvirt_network.create_or_del_network(net_attrs, is_del=True)
        firewalld.start()
