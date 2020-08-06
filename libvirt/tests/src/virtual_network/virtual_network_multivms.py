import logging
import time

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_package
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface


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
    check_ping = 'yes' == params.get('check_ping')
    expect_ping_host = 'yes' == params.get('expect_ping_host', 'no')
    expect_ping_out = 'yes' == params.get('expect_ping_out', 'no')
    expect_ping_vm = 'yes' == params.get('expect_ping_vm', 'no')
    out_ip = params.get('out_ip', 'www.redhat.com')
    live_update = 'yes' == params.get('live_update', 'no')
    set_all = 'yes' == params.get('set_all', 'no')

    rand_id = '_' + utils_misc.generate_random_string(3)
    bridge_name = params.get('bridge_name', 'test_br0') + rand_id
    iface_name = utils_net.get_net_if(state="UP")[0]
    test_net = 'net_isolated' + rand_id
    bridge_created = False

    vmxml_backup_list = []
    for vm_i in vm_list:
        vmxml_backup_list.append(vm_xml.VMXML.new_from_inactive_dumpxml(vm_i.name))

    try:
        # Test feature: port isolated
        if feature == 'port_isolated':
            if not libvirt_version.version_compare(6, 2, 0):
                test.cancel('Libvirt version should be'
                            ' > 6.2.0 to support port isolated')

            if case.startswith('set_iface'):
                create_bridge(bridge_name, iface_name)
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
                create_bridge(bridge_name, iface_name)
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

            # A map of vm session and vm's ip addr
            session_n_ip = {}
            for vm_i in vm_list:
                mac = vm_i.get_mac_address()
                sess = vm_i.wait_for_serial_login()
                vm_ip = utils_net.get_guest_ip_addr(sess, mac)
                session_n_ip[sess] = vm_ip
                logging.debug('Vm %s ip: %s', vm_i.name, vm_ip)

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
                        wait_remove_event=True,
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

    finally:
        for vmxml_i in vmxml_backup_list:
            vmxml_i.sync()
        virsh.net_undefine(test_net, debug=True)

        # Remove test bridge
        if bridge_created:
            cmd = 'tmux -c "ip link set {1} nomaster;  ip link delete {0};' \
                  'pkill dhclient; sleep 6; dhclient {1}"'.format(
                   bridge_name, iface_name)
            process.run(cmd, shell=True, verbose=True, ignore_status=True)
