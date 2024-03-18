import logging
import re

from virttest import utils_net
from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test start vm or hotplug interface with specific mac address
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    scenario = params.get('scenario')
    outside_ip = params.get('outside_ip')
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    set_mac = eval(params.get('mac', {})).get('mac_address')
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    expect_xml_mac = params.get('expect_xml_mac')
    expect_tap_mac = params.get('expect_tap_mac')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        if scenario == 'pre_set':
            iface = libvirt_vmxml.create_vm_device_by_type('interface',
                                                           iface_attrs)
            vmxml.add_device(iface)
            LOG.debug(f'Interface to add to vm:\n{iface}')

            if status_error:
                start_result = virsh.define(vmxml.xml, debug=True)
                libvirt.check_exit_status(start_result, status_error)
                if err_msg:
                    libvirt.check_result(start_result, expected_fails=err_msg)
                return
            else:
                vmxml.sync()
            vm.start()

        elif scenario == 'hotplug':
            vmxml.sync()
            vm.start()
            if set_mac:
                at_options = f'network default --mac {set_mac} --model virtio'
            else:
                at_options = f'network default --model virtio'

            at_result = virsh.attach_interface(vm_name, at_options, debug=True)
            libvirt.check_exit_status(at_result, status_error)
            if status_error:
                if err_msg:
                    libvirt.check_result(at_result, expected_fails=err_msg)
                return

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        iface_xml = vmxml.get_devices('interface')[0]
        LOG.debug(f'Interface in vmxml:\n{iface_xml}')
        vmxml_mac = iface_xml.mac_address
        if not re.match(expect_xml_mac, vmxml_mac):
            test.fail(f'Expect mac address on vmxml: {expect_tap_mac}, '
                      f'not {vmxml_mac}')

        virsh.domiflist(vm_name, **VIRSH_ARGS)
        iflist = libvirt.get_interface_details(vm_name)
        LOG.debug(f'iflist of vm: {iflist}')
        tap_on_host = iflist[0]['interface']
        LOG.debug(f'Tap device on host: {tap_on_host}')

        # Get the expected mac address of tap device from mac address on vmxml
        expect_tap_mac = re.sub('^\w+:', expect_tap_mac, vmxml_mac)
        LOG.debug(f'Expect mac address of tap device on host: {expect_tap_mac}')
        tap_mac = utils_net.get_linux_iface_info(iface=tap_on_host)['address']
        if not re.match(expect_tap_mac, tap_mac):
            test.fail(f'Expect mac address of tap device: {expect_tap_mac} '
                      f'not {tap_mac}')

        session = vm.wait_for_serial_login()
        ip_in_vm = session.cmd_output('ip addr')
        LOG.debug(f'ip addr output on vm:\n{ip_in_vm}')
        if vmxml_mac not in ip_in_vm:
            test.fail(f'Not found mac address from vmxml {vmxml_mac} in vm.')

        ips = {'outside_ip': outside_ip}
        network_base.ping_check(params, ips, session, force_ipv4=True)
        session.close()

    finally:
        bkxml.sync()
