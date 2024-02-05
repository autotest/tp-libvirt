import logging
import re
from avocado.utils import process

from virttest import utils_net
from virttest import utils_package
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check connectivity for mcast interface
    """
    vm_name = params.get('main_vm')
    vms = params.get('vms').split()
    vm, vm_c = (env.get_vm(vm_i) for vm_i in vms)

    mcast_addr = params.get('mcast_addr')
    iface_attrs = eval(params.get('iface_attrs', '{}'))
    iface_m_attrs = eval(params.get('iface_m_attrs', '{}'))
    expect_msg = params.get('expect_msg')

    bkxmls = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

    try:
        vmxml, vmxml_c = list(map(vm_xml.VMXML.new_from_inactive_dumpxml, vms))

        vmxml.del_device('interface', by_tag=True)
        vmxml_c.del_device('interface', by_tag=True)

        iface = libvirt_vmxml.create_vm_device_by_type(
            'interface', iface_attrs)
        iface_m = libvirt_vmxml.create_vm_device_by_type('interface',
                                                         iface_m_attrs)

        for vmxml_i in (vmxml, vmxml_c):
            for iface_i in (iface, iface_m):
                libvirt.add_vm_device(vmxml_i, iface_i)

        [LOG.debug(f'VMXML of {vm_x}:\n{virsh.dumpxml(vm_x).stdout_text}')
         for vm_x in vms]

        [vm_i.start() for vm_i in [vm, vm_c]]

        netstat_out = process.run('netstat -g').stdout_text
        if mcast_addr not in netstat_out:
            test.fail(f'Not found "{mcast_addr}" in netstat output')

        session = vm.wait_for_serial_login()
        session_c = vm_c.wait_for_serial_login()

        if any([not utils_package.package_install('omping', sess)
                for sess in (session, session_c)]):
            test.error('Failed to install "omping" in vms.')

        ip, ip_c = [vm_i.get_address() for vm_i in [vm, vm_c]]

        LOG.debug(session.cmd_output('ip a'))
        LOG.debug(session_c.cmd_output('ip a'))

        # Build omping command
        ip_suf, ip_suf_c = (ip_i.split('.')[-1] for ip_i in (ip, ip_c))
        ip_pre = '.'.join(ip.split('.')[:3])
        omping_cmd = f'omping -m {mcast_addr} {ip_pre}.{{{ip_suf},{ip_suf_c}}}'

        # omping from vm to vm_c
        session.cmd('systemctl stop firewalld')
        status, output = utils_net.raw_ping(omping_cmd, 5, session,
                                            output_func=LOG.debug)
        if 'response message never received' not in output:
            test.fail(f'omping failed due to "{output}"')

        # Keep omping process alive on vm then omping from vm_c to vm
        session.sendline(omping_cmd)
        session_c.cmd('systemctl stop firewalld')
        status_c, output_c = utils_net.raw_ping(omping_cmd, 5, session_c,
                                                output_func=LOG.debug)
        if not re.search(expect_msg, output_c):
            test.fail(f'Not found "{expect_msg}" in output of omping')

        session.close()
        session_c.close()

    finally:
        [backup_xml.sync() for backup_xml in bkxmls]
