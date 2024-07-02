import logging
import re

from avocado.utils import process
from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.network_xml import NetworkXML
from virttest.staging import service
from virttest.utils_test import libvirt

from provider.virtual_network import network_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test resolve guest hostname from host by resolvectl

    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vm_b = env.get_vm(params.get('vms').split()[-1])
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg', '')
    rand_id = utils_misc.generate_random_string(3)
    register = 'yes' == params.get('register', 'no')
    net_attrs = eval(params.get('net_attrs', '{}'))
    ips = eval(params.get('ips', '{}'))

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    net_xml = NetworkXML.new_from_net_dumpxml('default')
    bk_net_xml = net_xml.copy()
    sys_resolved = service.Factory.create_service("systemd-resolved")
    conf_path = '/etc/resolv.conf'
    conf_path_bk = f'{conf_path}.bk_{rand_id}'

    try:
        sys_resolved.start()
        iface_a = network_base.get_iface_xml_inst(vm_name, 'on vm')
        mac_a = iface_a.mac_address

        ip_attrs = eval(params.get('ip_attrs', '{}'))
        net_ips = net_xml.ips
        net_ips[0].update(**ip_attrs)
        net_xml.ips = net_ips

        net_xml.setup_attrs(**net_attrs)
        if status_error:
            net_xml.xmltreefile.write()
            LOG.debug(net_xml)
            def_result = virsh.net_define(net_xml.xml, debug=True)
            libvirt.check_exit_status(def_result, status_error)
            libvirt.check_result(def_result, err_msg)
            return

        net_xml.sync()
        LOG.debug(virsh.net_dumpxml('default').stdout_text)

        LOG.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        br = net_xml.bridge['name']
        domain_output = process.run(f'resolvectl domain {br}').stdout_text
        dns_output = process.run(f'resolvectl dns {br}').stdout_text
        if register:
            dom_pattern = re.compile(rf'Link \d+ \({br}\): ~example.com\s*$')
            dns_pattern = re.compile(
                rf'Link \d+ \({br}\): {net_ips[0].address}\s*$')
        else:
            dom_pattern = re.compile(r'Link \d+ \(.*\):\s*$')
            dns_pattern = dom_pattern

        if not dom_pattern.search(domain_output):
            test.fail(f'Unexpected output of resolvectl:\n{domain_output}')
        if not dns_pattern.search(dns_output):
            test.fail(f'Unexpected output of resolvectl:\n{dns_output}')

        # Backup the /etc/resolv.conf file
        process.run(f'cp {conf_path} {conf_path_bk}')
        # Make /etc/resolve.conf as symbolic file
        process.run(
            f'ln -f -r -s /run/systemd/resolve/stub-resolv.conf {conf_path}')
        process.run(f'file {conf_path}')
        name_serv_output = process.run(f'cat {conf_path}').stdout_text
        if not re.search(r'nameserver\s+127.0.0.53', name_serv_output):
            test.fail('Nameserver output check failed')

        vm.start()
        vm_b.start()
        # Wait for vm to get dhcp lease
        vm.wait_for_serial_login().close()

        session_b = vm_b.wait_for_serial_login()
        session_b.cmd('hostnamectl set-hostname vmtest')
        session_b.cmd('systemctl restart NetworkManager')
        session_b.close()

        virsh.net_dhcp_leases('default', **VIRSH_ARGS)
        network_base.ping_check(params, ips)

    finally:
        vm.destroy()
        vm_b.destroy()
        bkxml.sync()
        bk_net_xml.sync()
        process.run(f'mv {conf_path_bk} {conf_path}', ignore_status=True)
