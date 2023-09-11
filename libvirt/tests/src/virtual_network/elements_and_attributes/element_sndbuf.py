import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test 'sndbuf' element of interface
    """
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    outside_ip = params.get('outside_ip')
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    sndbuf = int(params.get('sndbuf'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_device('interface', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs)
        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        vm.start()
        session = vm.wait_for_serial_login()

        libvirt.check_qemu_cmd_line(f'"sndbuf":{sndbuf}')

        ips = {'outside_ip': outside_ip}
        network_base.ping_check(params, ips, session, force_ipv4=True)
        session.close()

    finally:
        bkxml.sync()
