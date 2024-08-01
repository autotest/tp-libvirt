import logging
import re

from avocado.utils import process
from virttest import utils_misc
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test 'coalesce' element of interface
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    outside_ip = params.get('outside_ip')
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg')
    br_type = params.get('br_type', '')
    max_frames = params.get('max_frames')
    rand_id = utils_misc.generate_random_string(3)
    br_name = br_type + '_' + rand_id

    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    iface_attrs = eval(params.get('iface_attrs', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if br_type == 'linux_br':
            utils_net.create_linux_bridge_tmux(br_name, host_iface)
            process.run(f'ip l show type bridge {br_name}', shell=True)
        elif br_type == 'ovs_br':
            utils_net.create_ovs_bridge(br_name)

        vmxml.del_device('interface', by_tag=True)
        iface = libvirt_vmxml.create_vm_device_by_type('interface', iface_attrs)
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

        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()
        virsh.domiflist(vm_name, **VIRSH_ARGS)
        iflist = libvirt.get_interface_details(vm_name)
        LOG.debug(f'iflist of vm: {iflist}')
        domif = iflist[0]['interface']
        LOG.debug(f'Domain interface: {domif}')

        eth_output = process.run(f'ethtool -c {domif}|grep rx-frames',
                                 shell=True).stdout_text
        max_frames = min(int(max_frames), 64)
        if re.search(f'rx-frames:\s+{max_frames}', eth_output):
            LOG.debug('ethtool check PASS')
        else:
            test.fail(
                f'"rx-frames: {max_frames}" not found in ethtool output.')

        session = vm.wait_for_serial_login()
        ips = {'outside_ip': outside_ip}
        network_base.ping_check(params, ips, session, force_ipv4=True)
        session.close()

    finally:
        bkxml.sync()
        if br_type == 'linux_br':
            utils_net.delete_linux_bridge_tmux(br_name, host_iface)
        if br_type == 'ovs_br':
            utils_net.delete_ovs_bridge(br_name)
