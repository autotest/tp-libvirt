import logging

from provider.virtual_network import network_base

from virttest import utils_libvirtd
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_libvirt import libvirt_service
from virttest.utils_libvirt import libvirt_vmxml

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check the connectivity for network interface type including nat, route,
    isolated, open(ipv4)
    """
    vms = params.get('vms').split()
    vm, ep_vm = (env.get_vm(vm_i) for vm_i in vms)
    forward_mode = params.get('forward_mode')
    network_attrs = eval(params.get('network_attrs'))
    iface_attrs = eval(params.get('iface_attrs'))
    outside_ip = params.get('outside_ip')

    bkxmls = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

    try:
        LOG.info('TEST_SETUP: confirm the firewalld is running, '
                 'if not, start firewalld and then '
                 'restart libvirtd or virtqemud)')
        if not libvirt_service.ensure_service_started('firewalld'):
            utils_libvirtd.libvirtd_restart()

        LOG.info('TEST_SETUP: define and start a virtual network with forward '
                 'mode')
        libvirt_network.create_or_del_network(network_attrs)
        LOG.debug(f'Network xml:\n'
                  f'{virsh.net_dumpxml(network_attrs["name"]).stdout_text}')

        LOG.info('TEST_SETUP: Prepare 2 vms with interface connected to this '
                 'network. One vm is the vm to be tested, another vm act as '
                 'endpoint.')
        vmxml, ep_vmxml = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))
        [libvirt_vmxml.modify_vm_device(vmxml_i, 'interface', iface_attrs)
         for vmxml_i in [vmxml, ep_vmxml]]

        LOG.info('TEST_STEP: Start the 2 VMs')
        [vm_inst.start() for vm_inst in [vm, ep_vm]]

        if forward_mode == 'open':
            LOG.debug('"open" mode: can not get ip address if firewalld is '
                      'running')
            return

        session, ep_session = (vm_inst.wait_for_login()
                               for vm_inst in [vm, ep_vm])
        mac, ep_mac = list(map(vm_xml.VMXML.get_first_mac_by_name, vms))

        ips_v4 = network_base.get_test_ips(session, mac, ep_session, ep_mac,
                                           network_attrs['name'],
                                           ip_ver='ipv4')
        ips_v6 = network_base.get_test_ips(session, mac, ep_session, ep_mac,
                                           network_attrs['name'],
                                           ip_ver='ipv6')
        ips_v4['outside_ip'] = outside_ip
        network_base.ping_check(params, ips_v4, session,
                                force_ipv4=True)
        network_base.ping_check(params, ips_v6, session,
                                force_ipv4=False)
    finally:
        [backup_xml.sync() for backup_xml in bkxmls]
        libvirt_network.create_or_del_network(network_attrs, is_del=True)
