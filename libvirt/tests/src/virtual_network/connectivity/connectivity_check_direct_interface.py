import logging

from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check connectivity for direct type interface
    """
    vm_name = params.get('main_vm')
    vms = params.get('vms').split()
    vm, ep_vm = (env.get_vm(vm_i) for vm_i in vms)
    outside_ip = params.get('outside_ip')
    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True).split()[0]
    iface_attrs = eval(params.get('iface_attrs', '{}'))

    bkxmls = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

    try:
        vmxml, ep_vmxml = list(
            map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))
        mac, ep_mac = list(map(vm_xml.VMXML.get_first_mac_by_name, vms))
        [vmxml_i.del_device('interface', by_tag=True) for vmxml_i in
         [vmxml, ep_vmxml]]

        libvirt_vmxml.modify_vm_device(
            vmxml, 'interface', {**iface_attrs, **{'mac_address': mac}})
        libvirt_vmxml.modify_vm_device(
            ep_vmxml, 'interface', {**iface_attrs, **{'mac_address': ep_mac}})

        [LOG.debug(f'VMXML of {vm_x}:\n{virsh.dumpxml(vm_x).stdout_text}')
         for vm_x in vms]

        [vm_i.start() for vm_i in [vm, ep_vm]]
        session, ep_session = (vm_inst.wait_for_serial_login()
                               for vm_inst in [vm, ep_vm])

        ips_v4 = network_base.get_test_ips(session, mac, ep_session, ep_mac,
                                           net_name=None,
                                           ip_ver='ipv4',
                                           host_iface=host_iface)
        ips_v6 = network_base.get_test_ips(session, mac, ep_session, ep_mac,
                                           net_name=None,
                                           ip_ver='ipv6',
                                           host_iface=host_iface)
        ips_v4['outside_ip'] = outside_ip
        ping_check_args = {'host_ping_outside': {'interface': host_iface}}

        network_base.ping_check(params, ips_v4, session, force_ipv4=True,
                                **ping_check_args)
        network_base.ping_check(params, ips_v6, session, force_ipv4=False,
                                **ping_check_args)

    finally:
        [backup_xml.sync() for backup_xml in bkxmls]
