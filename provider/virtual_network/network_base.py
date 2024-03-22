import logging
import re
import shutil

import aexpect
from avocado.core import exceptions
from avocado.utils import process
from virttest import remote
from virttest import utils_net
from virttest import virsh
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import vm_xml

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def get_test_ips(session, mac, ep_session, ep_mac, net_name=None,
                 ip_ver='ipv4', host_iface=None):
    """
    Get ips including vm ip, endpoint ip, host ip

    :param session: vm session
    :param mac: mac address of vm
    :param ep_session: endpoint vm session
    :param ep_mac: mac address of endpoint vm
    :param net_name: name of virtual network
    :param ip_ver: ip version, ipv4/ipv6
    :param host_iface: Host interface
    :return: a dict of ip addresses
    """
    ips = {}
    ips['vm_ip'], ips['ep_vm_ip'] = [utils_net.get_guest_ip_addr(
        sess, mac_ad, ip_version=ip_ver)
        for sess, mac_ad in [(session, mac), (ep_session, ep_mac)]]
    if not host_iface:
        host_public_ip = utils_net.get_host_ip_address(ip_ver=ip_ver)
    else:
        host_public_ip = utils_net.get_ip_address_by_interface(host_iface,
                                                               ip_ver=ip_ver)
    ips['host_public_ip'] = host_public_ip

    if net_name:
        net_inst = network_xml.NetworkXML.new_from_net_dumpxml(net_name)
        net_br = net_inst.bridge['name']
        ips['host_virbr_ip'] = utils_net.get_ip_address_by_interface(
            net_br, ip_ver=ip_ver)

    LOG.debug(f'IPs: {ips}')
    return ips


def ping_check(params, ips, session=None, force_ipv4=True, **args):
    """
    Ping between multiple targets and check results according to
    settings from params

    :param params: test params
    :param ips: a dict of ip addresses
    :param session: vm session to ping from
    :param force_ipv4: whether to force ping with ipv4
    :param args: other kwargs
    """
    outside_ip = ips.get('outside_ip')
    vm_ip = ips.get('vm_ip')
    ep_vm_ip = ips.get('ep_vm_ip')
    host_public_ip = ips.get('host_public_ip')
    host_virbr_ip = ips.get('host_virbr_ip')
    outside_ip = ips.get('outside_ip')
    ping_patterns = {k: v for k, v in params.items() if '_ping_' in k}

    for pattern, expect_result in ping_patterns.items():
        source, destination = pattern.split('_ping_')
        if destination == 'outside' and not force_ipv4:
            LOG.debug('No need to test ping outside with ipv6')
            continue
        dest_ip = eval(f'{destination}_ip')
        if dest_ip is None:
            raise exceptions.TestError(f'IP of {destination} is None')

        ping_args = args.get(pattern, {})

        LOG.info(f'TEST_STEP: Ping from {source} to {destination} '
                 f'(ip: {dest_ip})')
        ping_session = session if source == 'vm' else None
        status, _ = utils_net.ping(dest=dest_ip, count=5,
                                   timeout=10,
                                   session=ping_session,
                                   force_ipv4=force_ipv4,
                                   **ping_args)
        msg = f'Expect ping from {source} to {destination} should ' \
              f'{expect_result.upper()}, actual result is ' \
              f'{"PASS" if status == 0 else "FAIL"}'
        ping_result = (status == 0) == (expect_result == 'pass')
        if ping_result:
            LOG.debug(msg)
        else:
            raise exceptions.TestFail(msg)


def create_tap(tap_name, bridge_name, user):
    """
    Create tap device

    :param tap_name: name of tap device
    :param bridge_name: bridge to connect to
    :param user: user with access
    """
    # Create tap device with ip command
    tap_cmd = f'ip tuntap add mode tap user {user} group {user} name ' \
              f'{tap_name};ip link set {tap_name} up;' \
              f'ip link set {tap_name} master {bridge_name}'
    # Execute command as root
    process.run(tap_cmd, shell=True, verbose=True)


def create_macvtap(macvtap_name, iface, user):
    """
    Create macvtap device

    :param macvtap_name: name of macvtap device
    :param iface: host iface
    :param user: user with access
    """
    mac_addr = utils_net.generate_mac_address_simple()
    macvtap_cmd = f'ip link add link {iface} name {macvtap_name} address' \
                  f' {mac_addr} type macvtap mode bridge;' \
                  f'ip link set {macvtap_name} up'
    process.run(macvtap_cmd, shell=True)
    cmd_get_tap = f'ip link show {macvtap_name} | head -1 | cut -d: -f1'
    tap_index = process.run(cmd_get_tap, shell=True).stdout_text.strip()
    device_path = f'/dev/tap{tap_index}'
    logging.debug(f'device_path: {device_path}')
    # Check the current permissions
    process.run('ls -l %s' % device_path, shell=True)
    # Change owner and group for device
    process.run(f'chown {user} {device_path};chgrp {user} {device_path}',
                shell=True)
    # Check if device owner is changed to unprivileged user
    process.run('ls -l %s' % device_path, shell=True)

    return mac_addr


def set_tap_mtu(tap, mtu_size):
    """
    Set mtu for tap/macvtap device

    :param tap: tap/macvtap device name
    :param mtu_size: mtu size to set
    """
    process.run(f'ip link set dev {tap} mtu {mtu_size}')


def delete_tap(tap_name):
    """
    Delete tap/macvtap device with given name

    :param tap_name: name of tap/macvtap device
    """
    process.run(f'ip l del {tap_name}', shell=True, ignore_status=True)


def prepare_vmxml_for_unprivileged_user(up_user, root_vmxml):
    """
    Prepare vmxml for unprivileged user from vm of root user

    :param up_user: unprivileged user's name
    :param root_vmxml: vmxml of root user
    :return: vmxml instance for unprivileged user
    """
    # Create vm as unprivileged user
    LOG.info('Create vm as unprivileged user')
    upu_vmxml = root_vmxml.copy()

    upu_vmxml.vm_name = 'vm_' + up_user
    upu_vmxml.del_uuid()

    # Prepare vm for unprivileged user
    xml_devices = upu_vmxml.devices
    disks = xml_devices.by_device_tag("disk")
    for disk in disks:
        ori_path = disk.source['attrs'].get('file')
        if not ori_path:
            continue

        file_name = ori_path.split('/')[-1]
        new_disk_path = f'/home/{up_user}/{file_name}'
        LOG.debug(f'New disk path: {new_disk_path}')

        # Copy disk image file and chown to make sure that
        # unprivileged user has access
        shutil.copyfile(ori_path, new_disk_path)
        shutil.chown(new_disk_path, up_user, up_user)

        # Modify xml to set new path of disk
        disk_index = xml_devices.index(disk)
        xml_devices[disk_index].setup_attrs(
            **{'source': {'attrs': {'file': new_disk_path}}})
        LOG.debug(xml_devices[disk_index].source)

    upu_vmxml.devices = xml_devices

    # Remove nvram tag of os to avoid permission issue
    os_xml = upu_vmxml.os
    os_xml.del_nvram()
    upu_vmxml.os = os_xml

    return upu_vmxml


def define_vm_for_unprivileged_user(up_user, upu_vmxml):
    """
    Define vm for unprivileged user from given vmxml

    :param up_user: unprivileged user's name
    :param upu_vmxml: vmxml instance for unprivileged user
    """
    upu_args = {
        'unprivileged_user': up_user,
        'ignore_status': False,
        'debug': True,
    }

    new_xml_path = f'/home/{up_user}/upu.xml'
    shutil.copyfile(upu_vmxml.xml, new_xml_path)

    # Define vm for unprivileged user
    virsh.define(new_xml_path, **upu_args)
    LOG.debug(virsh.dumpxml(upu_vmxml.vm_name, **upu_args).stdout_text)


def unprivileged_user_login(unpr_vm_name, unpr_user, username, password,
                            timeout=240):
    """
    Login unprivileged vm as unprivileged user and get session

    :param unpr_vm_name: name of unprivileged vm
    :param unpr_user: name of unprivileged user
    :param username: username to login to vm
    :param password: password to login to vm
    :param timeout: timeout of login attempt
    :return: shell session instance of vm
    """
    cmd = f"su - {unpr_user} -c 'virsh console {unpr_vm_name}'"

    session = aexpect.ShellSession(cmd)
    session.sendline()
    remote.handle_prompts(session, username, password, r'[\#\$]\s*$', timeout)

    return session


def set_static_ip(iface, ip, netmask, session):
    """
    Set static ip addr/netmask for given iface of given session

    :param iface: iface name
    :param ip: ip to set
    :param netmask: netmask to set
    :param session: session to operate on
    """
    session.cmd('systemctl stop NetworkManager'),
    session.cmd(f'ifconfig {iface} {ip}/{netmask}')


def get_iface_xml_inst(vm_name, comment, index=0):
    """
    Get iface xml instance with given vm and index

    :param vm_name: name of vm
    :param comment: comment to log
    :param index: index of interface on vm, defaults to 0
    :return: xml instance of interface
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    iface = vmxml.get_devices('interface')[index]
    LOG.debug(f'Interface xml ({comment}):\n{iface}')
    return iface


def get_ethtool_coalesce(iface):
    """
    Get coalesce parameters from ethtool command output

    :param iface: interface name
    :return: dict-type coalesce parameters
    """
    eth_out = process.run(f'ethtool -c {iface}').stdout_text
    eth_out = re.sub("[\t ]+", "", eth_out)
    eth_out = re.sub("n/a", "0", eth_out)
    items = [x.split(':') for x in eth_out.splitlines() if x]
    coalesce = {item[0]: item[1].strip() for item in items[1:] if len(item) == 2}

    return coalesce


def check_iface_attrs(iface, key, expect_val):
    """
    Check whether interface attribute value meets expectation

    :param iface: interface name
    :param key: interface attribute key
    :param expect_val: expect attribute value
    """
    actual_val = iface.fetch_attrs().get(key)
    LOG.debug(f'Expect {key}: {expect_val}\n'
              f'Actual {key}: {actual_val}')
    if actual_val != expect_val:
        raise exceptions.TestFail(f'Updated {key} of iface should be '
                                  f'{expect_val}, NOT {actual_val}')
