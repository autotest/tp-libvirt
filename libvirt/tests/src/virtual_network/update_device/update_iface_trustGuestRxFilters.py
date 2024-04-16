import logging

from avocado.utils import process
from virttest import libvirt_version
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
    Test live update trustGuestRxFilters for direct type interface
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_net_if(
        state='UP')[0]
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    ips = {'outside_ip': params.get('outside_ip')}

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()

        iface = network_base.get_iface_xml_inst(vm_name, 'on vm')
        target_dev = iface.target['dev']
        update_attrs = eval(params.get('update_attrs', '{}'))
        LOG.debug(f'Update iface with attrs: {update_attrs}')
        iface.setup_attrs(**update_attrs)
        LOG.debug(f'Update iface with xml:\n{iface}')

        virsh.update_device(vm_name, iface.xml, **VIRSH_ARGS)

        iface_update = network_base.get_iface_xml_inst(vm_name, 'after update')
        LOG.debug(f'iface trustGuestRxFilters after update: '
                  f'{iface_update.trustGuestRxFilters}')
        if iface_update.trustGuestRxFilters != update_attrs[
                'trustGuestRxFilters']:
            test.fail(f'Interface trustGuestRxFilters not successfully updated '
                      f'to {update_attrs["trustGuestRxFilters"]}')

        mac_host = utils_net.get_linux_iface_info(iface=target_dev)['address']

        session = vm.wait_for_serial_login()
        mac = iface.mac_address
        vm_iface = utils_net.get_linux_ifname(session, mac)
        LOG.debug(f'Interface inside vm: {vm_iface}')

        vm_iface_info = utils_net.get_linux_iface_info(
            vm_iface, session=session)
        LOG.debug(f'iface info inside vm:\n{vm_iface_info}')
        new_mac = utils_net.generate_mac_address_simple()
        session.cmd(f'ip l set dev {vm_iface} address {new_mac}')

        network_base.ping_check(params, ips, session, force_ipv4=True)
        session.close()
        mac_host_after = utils_net.get_linux_iface_info(
            iface=target_dev)['address']

        expect_mac = mac_host if update_attrs[
            'trustGuestRxFilters'] == 'no' else new_mac
        if mac_host_after != expect_mac:
            test.fail(eval(params.get('err_msg')))

        virsh.shutdown(vm_name, **VIRSH_ARGS)
        if not vm.wait_for_shutdown():
            test.error('VM failed to shutdown in 60s')

        output = process.run(f' ip addr show {target_dev}',
                             shell=True, ignore_status=True)
        libvirt.check_exit_status(output, expect_error=True)
        libvirt.check_result(output, 'does not exist')

    finally:
        bkxml.sync()
