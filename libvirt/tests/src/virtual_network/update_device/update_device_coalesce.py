import logging

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


def check_coalesce_rx_frames(tap_device, rx_frames, test):
    """
    Check whether coalesce rx-frames meets expectation

    :param tap_device: tap device to be checked
    :param rx_frames: expected rx-frames
    :param test: test instance
    """
    coal_params = network_base.get_ethtool_coalesce(tap_device)
    LOG.debug(f'coalesce parameters from ethtool: {coal_params}')
    coal_rx_frames = str(coal_params.get('rx-frames')).strip()
    if coal_rx_frames != rx_frames:
        if rx_frames == '0' and coal_rx_frames == 'n/a':
            LOG.debug("The expected value is 0, but got n/a, which is "
                      "also acceptable.")
        else:
            test.fail(f'rx-frames of {tap_device} should be {rx_frames}, '
                      f'not {coal_rx_frames}')


def run(test, params, env):
    """
    Test update-device command to update coalesce of interface
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    rand_id = utils_misc.generate_random_string(3)
    br_type = params.get('br_type', '')
    br_name = br_type + '_' + rand_id
    mac = utils_net.generate_mac_address_simple()
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    rx_frames = params.get('rx_frames', '0')
    updated_rx_frames = params.get('updated_rx_frames', '0')
    updated_coalesce = eval(params.get('updated_coalesce', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        LOG.info('Create bridge for test.')
        if br_type == 'linux_br':
            utils_net.create_linux_bridge_tmux(br_name, host_iface)
            process.run(f'ip l show type bridge {br_name}', shell=True)
        elif br_type == 'ovs_br':
            utils_net.create_ovs_bridge(br_name)

        LOG.info('Setup interface on vm.')
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()
        session = vm.wait_for_serial_login()

        LOG.info('Check the rx-frames value for the tap device on the host.')
        iflist = libvirt.get_interface_details(vm_name)
        tap_device = iflist[0]['interface']
        check_coalesce_rx_frames(tap_device, rx_frames, test)

        LOG.info('Prepare interface xml to be updated with.')
        iface = network_base.get_iface_xml_inst(vm_name, 'on VM')
        if not updated_coalesce:
            iface.del_coalesce()
        else:
            iface.setup_attrs(**updated_coalesce)

        virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)

        LOG.info('Check the live xml is updated.')
        iface_update = network_base.get_iface_xml_inst(
            vm_name, 'after update-device')
        expect_coalesce = updated_coalesce.get('coalesce')
        network_base.check_iface_attrs(iface_update, 'coalesce',
                                       expect_coalesce)

        LOG.info('Check the rx-frames value for the tap device on the host.')
        check_coalesce_rx_frames(tap_device, updated_rx_frames, test)

        LOG.info('Login vm to check whether the network works well via ping.')
        ips = {'outside_ip': params.get('outside_ip')}
        network_base.ping_check(params, ips, session, force_ipv4=True)

    finally:
        if 'session' in locals():
            session.close()
        bkxml.sync()
        if br_type == 'linux_br':
            LOG.debug(f'Delete linux bridge: {br_name}')
            utils_net.delete_linux_bridge_tmux(br_name, host_iface)
        if br_type == 'ovs_br':
            LOG.debug(f'Delete ovs bridge: {br_name}')
            utils_net.delete_ovs_bridge(br_name)
