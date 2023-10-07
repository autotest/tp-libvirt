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
    vm, cli_vm = (env.get_vm(vm_i) for vm_i in vms)

    iface_attrs = eval(params.get('iface_attrs', '{}'))
    cli_iface_attrs = eval(params.get('cli_iface_attrs', '{}'))
    vm_ip = params.get('vm_ip')
    cli_vm_ip = params.get('cli_vm_ip')
    netmask = params.get('netmask')

    bkxmls = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

    try:
        vmxml, cli_vmxml = list(
            map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))
        [vmxml_i.del_device('interface', by_tag=True) for vmxml_i in
         [vmxml, cli_vmxml]]

        [libvirt_vmxml.modify_vm_device(vmxml_i, 'interface', attrs)
         for vmxml_i, attrs in [(vmxml, iface_attrs),
                                (cli_vmxml, cli_iface_attrs)]]

        [LOG.debug(f'VMXML of {vm_x}:\n{virsh.dumpxml(vm_x).stdout_text}')
         for vm_x in vms]

        [vm_i.start() for vm_i in [vm, cli_vm]]
        session, cli_session = (vm_inst.wait_for_serial_login()
                                for vm_inst in [vm, cli_vm])
        mac, cli_mac = list(map(vm_xml.VMXML.get_first_mac_by_name, vms))

        iface_info = utils_net.get_linux_iface_info(mac=mac, session=session)
        cli_iface_info = utils_net.get_linux_iface_info(
            mac=cli_mac, session=cli_session)

        vm_iface, cli_vm_iface = (info['ifname'] for info in
                                  (iface_info, cli_iface_info))
        LOG.debug(f'interface name of server vm: {vm_iface}\n'
                  f'client vm: {cli_vm_iface}')

        network_base.set_static_ip(vm_iface, vm_ip, netmask, session)
        network_base.set_static_ip(
            cli_vm_iface, cli_vm_ip, netmask, cli_session)

        status, _ = utils_net.ping(dest=cli_vm_ip, count=5,
                                   timeout=10,
                                   session=session,
                                   force_ipv4=True,)
        if status != 0:
            test.fail('Ping from server to client failed')

        status, _ = utils_net.ping(dest=vm_ip, count=5,
                                   timeout=10,
                                   session=cli_session,
                                   force_ipv4=True,)
        if status != 0:
            test.fail('Ping from client to server failed')
    finally:
        [backup_xml.sync() for backup_xml in bkxmls]
