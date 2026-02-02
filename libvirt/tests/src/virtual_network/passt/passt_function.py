import logging
import os
import shutil
import time
import json

from avocado.utils import process

import aexpect
from virttest import libvirt_version
from virttest import remote
from virttest import utils_net
from virttest import utils_selinux
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.staging import utils_memory
from virttest.utils_libvirt import libvirt_unprivileged
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_config import VirtQemudConfig
from virttest import utils_libvirtd

from provider.virtual_network import network_base
from provider.virtual_network import passt

LOG = logging.getLogger('avocado.' + __name__)

IPV6_LENGTH = 128


def run(test, params, env):
    """
    Test passt function
    """
    libvirt_version.is_libvirt_feature_supported(params)
    root = 'root_user' == params.get('user_type', '')
    if root:
        vm_name = params.get('main_vm')
        vm = env.get_vm(vm_name)
        virsh_ins = virsh
        log_dir = params.get('log_dir')
        user_id = params.get('user_id')
    else:
        vm_name = params.get('unpr_vm_name')
        test_user = params.get('test_user', '')
        test_passwd = params.get('test_passwd', '')
        user_id = passt.get_user_id(test_user)
        unpr_vm_args = {
            'username': params.get('username'),
            'password': params.get('password'),
        }
        vm = libvirt_unprivileged.get_unprivileged_vm(vm_name, test_user,
                                                      test_passwd,
                                                      **unpr_vm_args)
        uri = f'qemu+ssh://{test_user}@localhost/session'
        virsh_ins = virsh.Virsh(uri=uri)
        host_session = aexpect.ShellSession('su')
        remote.VMManager.set_ssh_auth(host_session, 'localhost', test_user,
                                      test_passwd)
        host_session.close()

    host_iface = params.get('host_iface')
    host_iface = host_iface if host_iface else utils_net.get_default_gateway(
        iface_name=True, force_dhcp=True, json=True)
    host_ip = utils_net.get_ip_address_by_interface(host_iface, ip_ver='ipv4')
    host_ip_v6 = utils_net.get_ip_address_by_interface(host_iface, ip_ver='ipv6')
    params['host_ip_v6'] = host_ip_v6
    params['socket_dir'] = socket_dir = eval(params.get('socket_dir'))
    params['proc_checks'] = proc_checks = eval(params.get('proc_checks', '{}'))
    mtu = params.get('mtu')
    outside_ip = params.get('outside_ip')
    log_file = f'/run/user/{user_id}/passt.log'
    iface_attrs = eval(params.get('iface_attrs'))
    iface_attrs['backend']['logFile'] = log_file
    vhostuser = 'yes' == params.get('vhostuser', 'no')
    multiple_nexthops = 'yes' == params.get('multiple_nexthops', 'no')
    libvirtd_debug_file = params.get('libvirtd_debug_file')
    warning_check = params.get('warning_check')
    use_domain_name_opts = params.get_boolean('use_domain_name_opts')

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name,
                                                   virsh_instance=virsh_ins)
    bkxml = vmxml.copy()
    shp_orig_num = utils_memory.get_num_huge_pages()

    selinux_status = passt.ensure_selinux_enforcing()
    passt.check_socat_installed()

    firewalld = service.Factory.create_service("firewalld")
    try:
        if root:
            passt.make_log_dir(user_id, log_dir)

        default_gw = utils_net.get_default_gateway()
        default_gw_v6 = utils_net.get_default_gateway(ip_ver='ipv6')

        vmxml.del_device('interface', by_tag=True)
        vmxml.sync(virsh_instance=virsh_ins)
        if vhostuser:
            # update vm xml with shared memory and vhostuser interface
            vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared", hpgs=False, memfd=True, vmxml=vmxml)
            iface_attrs['type_name'] = 'vhostuser'
            # update for virtqemud log
            virtqemud_config = VirtQemudConfig()
            virtqemud_config.log_outputs = "1:file:{0}".format(libvirtd_debug_file)
            utils_libvirtd.Libvirtd('virtqemud').restart()

        if use_domain_name_opts:
            hostname = params.get('hostname')
            fqdn = params.get('fqdn')
            iface_attrs['backend']['hostname'] = hostname
            iface_attrs['backend']['fqdn'] = fqdn

        libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs,
                                       virsh_instance=virsh_ins)
        LOG.debug(virsh_ins.dumpxml(vm_name).stdout_text)
        mac = vm.get_virsh_mac_address()

        vm.start()
        passt.check_proc_info(params, log_file, mac)

        #   check the passt log
        LOG.debug(virsh.dumpxml(vm_name))
        if not os.path.exists(log_file):
            test.fail(f'Logfile of passt "{log_file}" not created')

        session = vm.wait_for_serial_login(timeout=360)
        vm_iface = utils_net.get_linux_ifname(session, mac)
        passt.check_vm_ip(iface_attrs, session, host_iface, vm_iface)
        passt.check_vm_mtu(session, vm_iface, mtu)
        passt.check_default_gw(session, host_iface, multiple_nexthops)
        passt.check_nameserver(session)
        if use_domain_name_opts:
            session.cmd(params.get("catch_packets_backend_cmd"), ignore_all_errors=True)
            time.sleep(params.get_numeric("cmd_wait"))
            session.cmd(params.get("gen_packets_cmd"), ignore_all_errors=True)
            time.sleep(params.get_numeric("cmd_wait"))
            session.cmd(params.get("kill_backend_cmd"), ignore_all_errors=True)
            dhcp_packets = json.loads(session.cmd_output_safe("cat {0}".format(params.get("packets_log_file"))))
            for field in params.get_list("tshark_fields"):
                field_vals = []
                for packet in dhcp_packets:
                    # See DHCP json packets at https://gitlab.com/wireshark/wireshark/-/blob/master/test/baseline/dhcp.json
                    field_vals.extend(packet.get('_source', {}).get('layers', {}).get(field, []))

                field_vals = list(set(field_vals))
                if len(field_vals) != 1:
                    test.fail(
                        'Expect to get only 1 value from dhcp field {0} but get {1} in fact. Packets in json:\n{2}'.format(field, field_vals, dhcp_packets))

                if field == params.get("tshark_hostname"):
                    if hostname not in field_vals[0]:
                        test.fail('The hostname={0} in domain interface XML does not match the result {1}={2} in tshark'.format(hostname, field, field_vals[0]))
                else:
                    # Note: the fqdn with trailing dot will cause this check fail. It is a wireshark dissectors issue
                    # E.g: fqdn="xxx.xxx." . Wireshark will represent it like dhcp_fqdn_name="xxx.xxx"
                    # see also https://bugs.passt.top/show_bug.cgi?id=170
                    if fqdn not in field_vals[0]:
                        test.fail('The fqdn={0} in domain interface XML does not match the result {1}={2} in tshark'.format(fqdn, field, field_vals[0]))

        ips = {
            'outside_ip': outside_ip,
            'host_public_ip': host_ip,
        }
        network_base.ping_check(params, ips, session, force_ipv4=True)
        session.close()

        firewalld.stop()
        LOG.debug(f'Status of firewalld: {firewalld.status()}')
        passt.check_connection(vm, vm_iface,
                               ['TCP4', 'TCP6', 'UDP4', 'UDP6'],
                               host_iface)

        if 'portForwards' in iface_attrs:
            passt.check_portforward(vm, host_ip, params, host_iface)

        if vhostuser and warning_check:
            # Exclude the warning like: 'warning : qemuDomainObjTaintMsg:5529 : Domain ... is tainted: custom-monitor'
            check_warning = process.run('grep -v "qemuDomainObjTaintMsg.*custom-monitor" {0}|grep "warning :"'.format(libvirtd_debug_file),
                                        shell=True,
                                        ignore_status=True,
                                        logger=LOG)
            if check_warning.exit_status == 0:
                test.fail('Get warnings in virtqemud log: {0}'.format(check_warning.stdout_text))

        vm_sess = vm.wait_for_serial_login(timeout=360)
        vm_sess.cmd('systemctl start firewalld')
        vm.destroy()

        passt.check_passt_pid_not_exist()

    finally:
        firewalld.start()
        bkxml.sync(virsh_instance=virsh_ins)
        if root:
            shutil.rmtree(log_dir)
        if vhostuser:
            virtqemud_config.restore()
            process.run("rm {0}".format(libvirtd_debug_file), shell=True, ignore_status=True)
        utils_selinux.set_status(selinux_status)
