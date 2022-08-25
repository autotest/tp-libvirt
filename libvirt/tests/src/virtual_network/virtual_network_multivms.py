import logging as log
import time
import re
import sys

from avocado.utils import process
from avocado.utils import stacktrace

from virttest import libvirt_version
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_package
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def create_bridge(br_name, iface_name):
    """
    Create bridge attached to physical interface

    :param br_name: bridge to be created
    :param iface_name: physical interface name
    :return:
    """
    # Make sure the bridge not exist
    if libvirt.check_iface(br_name, "exists", "--all"):
        return

    # Create bridge using commands
    utils_package.package_install('tmux')
    cmd = 'tmux -c "ip link add name {0} type bridge; ip link set {1} up;' \
          ' ip link set {1} master {0}; ip link set {0} up; pkill dhclient; ' \
          'sleep 6; dhclient {0}; ifconfig {1} 0"'.format(br_name, iface_name)
    process.run(cmd, shell=True, verbose=True)


def ping_func(session, **expect):
    """
    Check whether ping result meet expectation

    :param session: session to ping from
    :param expect: dict-type,
                    {ip_1: True, ip_2: True}
                    'ip - expectation' map of ping result expectations
    :return: True if all ping results meet expectation, False if not
    """

    def _ping_check(ip, session):
        status, output = utils_net.ping(ip, count=5, timeout=10,
                                        session=session, force_ipv4=True)
        return True if status == 0 else False

    ping_result = {k: None for k in expect.keys()}

    result = True
    for ip in expect.keys():
        ping_result[ip] = _ping_check(ip, session)
        logging.debug('Expect ping result to %s: %s', ip, expect[ip])
        logging.debug('Actual ping result to %s: %s', ip, ping_result[ip])
        if ping_result[ip] != expect[ip]:
            result = False
            logging.error('Ping result to %s does not match expectation', ip)

    return result


def prepare_network(net_name, **kwargs):
    """
    Prepare a new network for test by creating net xml, defining network
    and starting network

    :param net_name: net to be created
    :param kwargs: params of net
    :return:
    """
    new_net = libvirt.create_net_xml(net_name, kwargs)
    virsh.net_define(new_net.xml, debug=True, ignore_status=False)
    virsh.net_start(net_name, debug=True, ignore_status=False)
    logging.debug(virsh.net_dumpxml(net_name).stdout_text)


def check_br_tap_params(br_name, tap_name, libvirt_manage=True):
    """
    When macTableManager is set to libvirt, libvirt disables kernel management
    of the MAC table (in the case of the Linux host bridge, this means enabling
    vlan_filtering on the bridge, and disabling learning and unicast_filter for
    all bridge ports), and explicitly adds/removes entries to the table
    according to the MAC addresses in the domain interface configurations.
    The bridge's vlan_filtering should be 1, and the tap device connected to
    this bridge has unicast_flood and learning set to '0'.

    :param br_name: string, name of the bridge
    :param tap_name: string, name of the tap device
    :param libvirt_manage: bool, whether MAC table is managed by libvirt
    :return: True or False
    """
    cmd1 = 'cat /sys/class/net/%s/bridge/vlan_filtering' % br_name
    cmd2 = 'cat /sys/class/net/%s/brif/%s/learning' % (br_name, tap_name)
    cmd3 = 'cat /sys/class/net/%s/brif/%s/unicast_flood' % (br_name, tap_name)
    try:
        vlan_filtering = process.run(cmd1, ignore_status=True,
                                     shell=True).stdout_text.strip()
        learning = process.run(cmd2, ignore_status=True,
                               shell=True).stdout_text.strip()
        unicast_flood = process.run(cmd3, ignore_status=True,
                                    shell=True).stdout_text.strip()
        logging.debug("bridge's vlan_filtering is {1}, {0} learning is {2}, "
                      "unicast_flood is {3}".format(tap_name, vlan_filtering,
                                                    learning, unicast_flood))
        if libvirt_manage:
            assert vlan_filtering == '1'
            assert learning == '0'
            assert unicast_flood == '0'
        else:
            assert vlan_filtering == '0'
            assert learning == '1'
            assert unicast_flood == '1'
    except AssertionError:
        stacktrace.log_exc_info(sys.exc_info())
        return False
    return True


def check_fdb(mac, libvirt_manage=True):
    """
    Check the bridge's forwarding database table for specific mac. If it's
    managed by libvirt, the record should be static.

    :params mac: string, mac address to be checked in the forward database table
    :params libvirt_manage: bool, if forward database table managed by libvirt
    :params return: True or False. True if get expected result
    """
    cmd = 'bridge fdb show | grep %s' % mac
    out = process.run(cmd, ignore_status=True, shell=True).stdout_text.strip()
    logging.debug("The related record in fdb table is:\n%s", out)
    res = re.search(r'%s.*vlan.*?static\n%s.*static' %
                    (mac, mac), out)
    if libvirt_manage:
        if not res:
            logging.debug("Can't find correct fdb record!")
            return False
    else:
        if res:
            logging.debug("libvirt do not manage the mactable, "
                          "but there is static fdb record!")
            return False
    return True


def run(test, params, env):
    """
    Test network/interface function on 2 vms:

        - Test settings on 2 vms
        - Run ping check on 2 vms including pinging each other
        ...

    """
    vms = params.get('vms').split()
    vm_list = [env.get_vm(v_name) for v_name in vms]
    if len(vm_list) != 2:
        test.cancel('More or less than 2 vms is currently unsupported')

    feature = params.get('feature', '')
    case = params.get('case', '')
    create_linux_bridge = 'yes' == params.get('create_linux_bridge', 'no')
    check_ping = 'yes' == params.get('check_ping')
    expect_ping_host = 'yes' == params.get('expect_ping_host', 'no')
    expect_ping_out = 'yes' == params.get('expect_ping_out', 'no')
    expect_ping_vm = 'yes' == params.get('expect_ping_vm', 'no')
    out_ip = params.get('out_ip', 'www.redhat.com')
    live_update = 'yes' == params.get('live_update', 'no')
    set_all = 'yes' == params.get('set_all', 'no')

    rand_id = '_' + utils_misc.generate_random_string(3)
    resume_vm = 'yes' == params.get('resume_vm', 'no')
    bridge_name = params.get('bridge_name', 'test_br0') + rand_id
    iface_name = utils_net.get_net_if(state="UP")[0]
    test_net = feature + rand_id
    bridge_created = False

    def pause_resume_vm(vm_name):
        """
        Use negative cmd to trigger guest io error to paused, then resume it
        :params vm_name: string, name of the vm
        :params return: None
        """
        cmd_ = '''virsh qemu-monitor-command %s '{"execute":"stop"}' ''' % vm_name
        out = process.run(cmd_, shell=True).stdout_text.strip()
        logging.debug("The out of the qemu-monitor-command is %s", out)
        vm = env.get_vm(vm_name)
        if not vm.is_paused():
            test.error("VM %s is not paused by qemu-monitor-command!" % vm_name)
        res = virsh.resume(vm_name, debug=True)
        libvirt.check_exit_status(res)

    vmxml_backup_list = []
    for vm_i in vm_list:
        vmxml_backup_list.append(vm_xml.VMXML.new_from_inactive_dumpxml(vm_i.name))
        # On some specific arch guest e.g s390x, dhcp-client package may not be installed by
        # default, enforce this logic here
        if vm_i.is_dead():
            vm_i.start()
        session = vm_i.wait_for_login()
        if not utils_package.package_install('dhcp-client', session):
            test.error("Failed to install dhcp-client on guest.")
        session.close()
        vm_i.destroy(gracefully=False)
    try:
        # Test feature: port isolated
        if feature == 'port_isolated':
            if not libvirt_version.version_compare(6, 2, 0):
                test.cancel('Libvirt version should be'
                            ' > 6.2.0 to support port isolated')

            if case.startswith('set_iface'):
                utils_net.create_linux_bridge_tmux(bridge_name, iface_name)
                bridge_created = True
                iface_type = case.split('_')[-1]
                if iface_type == 'network':
                    net_dict = {'net_forward': "{'mode': 'bridge'}",
                                'net_bridge': "{'name': '%s'}" % bridge_name}
                    prepare_network(test_net, **net_dict)
                    updated_iface_dict = {
                        'type': iface_type,
                        'source': "{'network': '%s'}" % test_net,
                    }
                elif iface_type == 'bridge':
                    updated_iface_dict = {
                        'type': iface_type,
                        'source': "{'bridge': '%s'}" % bridge_name,
                    }
                else:
                    test.error('Unsupported iface type: %s' % iface_type)

                # Set 2 vms to isolated=yes or set one to 'yes', the other to 'no'
                isolated_settings = ['yes'] * 2 if set_all else ['yes', 'no']
                for i in (0, 1):
                    vm_i = vm_list[i]
                    new_iface_dict = dict(
                        list(updated_iface_dict.items()) +
                        [('port', "{'isolated': '%s'}" % isolated_settings[i])]
                    )
                    libvirt.modify_vm_iface(vm_i.name, 'update_iface',
                                            new_iface_dict)
                    logging.debug(virsh.dumpxml(vm_i.name).stdout_text)

            if case == 'update_iface':
                if params.get('iface_port'):
                    iface_dict = {'port': params['iface_port']}
                    for vm_i in vm_list:
                        libvirt.modify_vm_iface(vm_i.name, 'update_iface', iface_dict)
                        logging.debug(virsh.dumpxml(vm_i.name).stdout_text)
                if live_update:
                    for vm_i in vm_list:
                        vm_i.start()

                # Test Update iface with new attrs
                new_iface_dict = {}
                if params.get('new_iface_port'):
                    new_iface_dict['port'] = params['new_iface_port']
                elif params.get('del_port') == 'yes':
                    new_iface_dict['del_port'] = True
                for vm_i in vm_list:
                    updated_iface = libvirt.modify_vm_iface(vm_i.name, 'get_xml', new_iface_dict)
                    result = virsh.update_device(vm_i.name, updated_iface, debug=True)
                    libvirt.check_exit_status(result)

            if case == 'attach_iface':
                new_ifaces = {}
                for vm_i in vm_list:
                    # Create iface xml to be attached
                    new_iface = interface.Interface('network')
                    new_iface.xml = libvirt.modify_vm_iface(
                        vm_i.name, 'get_xml',
                        {'port': params.get('new_iface_port')}
                    )
                    new_ifaces[vm_i.name] = new_iface

                    # Remove current ifaces on vm
                    vmxml_i = vm_xml.VMXML.new_from_inactive_dumpxml(vm_i.name)
                    vmxml_i.remove_all_device_by_type('interface')
                    vmxml_i.sync()
                    logging.debug(virsh.dumpxml(vm_i.name).stdout_text)

                    # Start vm for hotplug
                    vm_i.start()
                    session = vm_i.wait_for_serial_login()

                    # Hotplug iface
                    virsh.attach_device(vm_i.name, new_iface.xml, debug=True, ignore_status=False)

                    # Wait a few seconds for interface to be fully attached
                    time.sleep(5)
                    ip_l_before = session.cmd_output('ip l')
                    logging.debug(ip_l_before)
                    session.close()

            if case == 'set_network':
                utils_net.create_linux_bridge_tmux(bridge_name, iface_name)
                bridge_created = True
                net_dict = {
                    'net_forward': "{'mode': 'bridge'}",
                    'net_bridge': "{'name': '%s'}" % bridge_name,
                    'net_port': "{'isolated': '%s'}" % params.get(
                        'net_isolated', 'yes')}
                prepare_network(test_net, **net_dict)

                # Modify iface to connect to newly added network
                updated_iface_dict = {'type': 'network',
                                      'source': "{'network': '%s'}" % test_net}
                for vm_i in vm_list:
                    libvirt.modify_vm_iface(vm_i.name, 'update_iface',
                                            updated_iface_dict)
                    logging.debug(virsh.domiflist(vm_i.name).stdout_text)

        if feature == 'macTableManager':
            # create network with macTableManager='libvirt'
            if create_linux_bridge:
                utils_net.create_linux_bridge_tmux(bridge_name, iface_name)
                bridge_created = True
                net_dict = {
                    'net_forward': "{'mode': 'bridge'}",
                    'net_bridge': "{'name': '%s', 'macTableManager': 'libvirt'}" % bridge_name,
                    }
            else:
                net_dict = {
                    'net_forward': "{'mode': 'nat'}",
                    'net_bridge': "{'name': '%s', 'macTableManager': 'libvirt'}" % bridge_name,
                    'net_ip_address': params.get('net_ip_address'),
                    'net_ip_netmask': params.get('net_ip_netmask'),
                    'dhcp_start_ipv4': params.get('dhcp_start_ipv4'),
                    'dhcp_end_ipv4': params.get('dhcp_end_ipv4')
                }
            iface_dict = {'type': 'network', 'del_mac': True,
                          'source': "{'network': '%s'}" % test_net}
            prepare_network(test_net, **net_dict)
            for i in (0, 1):
                vm_i = vm_list[i]
                libvirt.modify_vm_iface(vm_i.name, 'update_iface', iface_dict)
                logging.debug("After modify vm interface, check vm xml:\n%s",
                              virsh.dumpxml(vm_i.name).stdout_text)
        # Check ping result from vm session to host, outside, the other vm
        if check_ping:
            for vm_i in vm_list:
                if vm_i.is_dead():
                    vm_i.start()
            host_ip = utils_net.get_host_ip_address()
            ping_expect = {
                host_ip: expect_ping_host,
                out_ip: expect_ping_out,
            }
            if resume_vm:
                for vm_i in vm_list:
                    vm_i.wait_for_serial_login().close()
                    pause_resume_vm(str(vm_i.name))
            # A map of vm session and vm's ip addr
            session_n_ip = {}
            for vm_i in vm_list:
                mac = vm_xml.VMXML.get_first_mac_by_name(vm_i.name)
                sess = vm_i.wait_for_serial_login()
                vm_ip = utils_net.get_guest_ip_addr(sess, mac)
                session_n_ip[sess] = vm_ip
                logging.debug('Vm %s ip: %s', vm_i.name, vm_ip)
                if not vm_ip:
                    test.error("Got vm %s ip as None!" % vm_i.name)

            # Check ping result from each vm's session
            for i in (0, 1):
                sess = list(session_n_ip.keys())[i]
                another_sess = list(session_n_ip.keys())[1 - i]
                ping_expect[session_n_ip[another_sess]] = expect_ping_vm
                if not ping_func(sess, **ping_expect):
                    test.fail('Ping check failed')
                # Remove the other session's ip from ping result, then the
                # next round of ping check will not do a ping check to the vm itself
                ping_expect.pop(session_n_ip[another_sess])

        # Some test steps after ping check
        if feature == 'port_isolated':
            if case == 'attach_iface':
                # Test detach of iface
                for vm_name_i in new_ifaces:
                    virsh.detach_device(
                        vm_name_i,
                        new_ifaces[vm_name_i].xml,
                        wait_for_event=True,
                        debug=True, ignore_status=False
                    )

                # Check whether iface successfully detached by checking 'ip l' output
                for vm_i in vm_list:
                    session = vm_i.wait_for_serial_login()
                    ip_l_after = session.cmd_output('ip l')
                    session.close()
                    if len(ip_l_before.splitlines()) == len(ip_l_after.splitlines()):
                        test.fail('Output of "ip l" is not changed afte detach, '
                                  'interface not successfully detached')
        if feature == 'macTableManager':
            for vm_i in vm_list:
                iface_mac = vm_xml.VMXML.get_first_mac_by_name(vm_i.name)
                tap_name = libvirt.get_ifname_host(vm_i.name, iface_mac)
                logging.debug("Check %s's params for vm %s", tap_name, vm_i.name)
                res1 = check_br_tap_params(bridge_name, tap_name)
                res2 = check_fdb(iface_mac)
                if not res1 or (not res2):
                    test.fail("macTableManager function check failed!")
    finally:
        for vmxml_i in vmxml_backup_list:
            vmxml_i.sync()
        virsh.net_undefine(test_net, debug=True)
        virsh.net_destroy(test_net, debug=True)

        # Remove test bridge
        if bridge_created:
            cmd = 'tmux -c "ip link set {1} nomaster;  ip link delete {0};' \
                  'pkill dhclient; sleep 6; dhclient {1}"'.format(
                   bridge_name, iface_name)
            process.run(cmd, shell=True, verbose=True, ignore_status=True)
