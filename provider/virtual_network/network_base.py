import logging
import math
import os
import re
import shutil

import aexpect
from avocado.core import exceptions
from avocado.utils import process
from virttest import remote
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_package
from virttest import virsh
from virttest.utils_libvirt import libvirt_vmxml
from virttest.libvirt_xml import network_xml
from virttest.libvirt_xml import vm_xml
from provider.bootc_image_builder import bootc_image_build_utils
from virttest.utils_libvirt import libvirt_network

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def get_vm_ip(session, mac, ip_ver="ipv4", timeout=5, ignore_error=False):
    """
    Get vm ip address

    :param session: vm session
    :param mac: mac address of vm
    :param ip_ver: ip version, defaults to "ipv4"
    :param ignore_error: True to return None, False to raise exception,
           defaults to False
    :return: ip address of given mac
    """
    def _get_vm_ip():
        iface_info = utils_net.get_linux_iface_info(mac=mac, session=session)
        addr_list = iface_info['addr_info']
        if ip_ver == "ipv4":
            target_addr = [addr for addr in addr_list if addr['family'] == 'inet']
        elif ip_ver == "ipv6":
            target_addr = [addr for addr in addr_list if addr['family'] == 'inet6'
                           and addr['scope'] == 'global'
                           and addr.get('mngtmpaddr') is not True]

        if len(target_addr) == 0:
            LOG.warning(f'No ip addr of given mac: {mac}')
            return
        elif len(target_addr) > 1:
            LOG.warning(f'Multiple ip addr: {target_addr}')

        return target_addr[0]['local']

    vm_ip = utils_misc.wait_for(_get_vm_ip, timeout, ignore_errors=True)

    if not vm_ip and not ignore_error:
        raise exceptions.TestError(
            f'Cannot find {ip_ver} addr with given mac: {mac}')

    return vm_ip


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
    ips['vm_ip'] = get_vm_ip(session, mac, ip_ver=ip_ver)
    ips['ep_vm_ip'] = get_vm_ip(ep_session, ep_mac, ip_ver=ip_ver)
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
    ping_patterns = {k: v for k, v in params.items() if '_ping_' in k}
    failures = []

    for pattern, expect_result in ping_patterns.items():
        source, destination = pattern.split('_ping_')
        if destination == 'outside' and not force_ipv4:
            LOG.debug('No need to test ping outside with ipv6')
            continue
        dest_ip = ips.get(f'{destination}_ip')
        if dest_ip is None:
            raise exceptions.TestError(f'IP of {destination} is None')

        ping_args = args.get(pattern, {})

        LOG.info(f'TEST_STEP: Ping from {source} to {destination} '
                 f'(ip: {dest_ip})')
        ping_session = session if source == 'vm' else None

        def _ping():
            """ Helper function to make use of wait_for """
            status, output = utils_net.ping(dest=dest_ip, count=5,
                                            timeout=10,
                                            session=ping_session,
                                            force_ipv4=force_ipv4,
                                            **ping_args)
            if status and "unreachable" in output and expect_result == 'pass':
                return False
            return status == 0

        status = utils_misc.wait_for(_ping, timeout=15)
        ping_result = (status is True) == (expect_result == 'pass')
        msg = f'Expect ping from {source} to {destination} should ' \
              f'{expect_result.upper()}, actual result is ' \
              f'{"PASS" if ping_result else "FAIL"}'
        if ping_result:
            LOG.debug(msg)
        else:
            LOG.error(msg)
            failures.append(msg)
    if failures:
        raise exceptions.TestFail('.'.join(failures))


def create_tap(tap_name, bridge_name, user, flag=''):
    """
    Create tap device

    :param tap_name: name of tap device
    :param bridge_name: bridge to connect to
    :param user: user with access
    """
    # Create tap device with ip command
    tap_cmd = f'ip tuntap add mode tap user {user} group {user} name ' \
              f'{tap_name} {flag};ip link set {tap_name} up;' \
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


def get_iface_xml_inst(vm_name, comment, index=0, options='', virsh_ins=virsh):
    """
    Get iface xml instance with given vm and index

    :param vm_name: name of vm
    :param comment: comment to log
    :param index: index of interface on vm, defaults to 0
    :return: xml instance of interface
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name, options=options,
                                          virsh_instance=virsh_ins)
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


def preparation_for_iface(iface_type, params):
    """
    Prepare the interface environment based on interface type

    :param iface_type: type of interface (network, bridge, ethernet, direct)
    :param params: additional parameters from test configuration
    """
    if iface_type == "bridge":
        bridge_name = params.get('bridge_name', 'br0')
        host_iface = params.get('host_iface')
        utils_net.create_linux_bridge_tmux(bridge_name, host_iface)

    elif iface_type == "ethernet":
        tap_name = params.get('tap_name', 'mytap0')
        user = params.get("user", "root")
        bridge_name = params.get('bridge_name', 'virbr0')
        queues_flag = params.get("queues_flag")
        create_tap(tap_name, bridge_name, user, flag=queues_flag)

    elif iface_type == "network":
        network_dict = eval(params.get("network_dict", "[]"))
        if network_dict:
            libvirt_network.create_or_del_network(network_dict)
            virsh.net_list("--all", debug=True)


def cleanup_for_iface(iface_type, params):
    """
    Clean up the interface environment based on interface type

    :param iface_type: type of interface (network, bridge, ethernet, direct)
    :param params: additional parameters from test configuration
    """
    if iface_type == "bridge":
        bridge_name = params.get('bridge_name', 'br0')
        host_iface = params.get('host_iface')
        utils_net.delete_linux_bridge_tmux(bridge_name, host_iface)

    elif iface_type == "ethernet":
        tap_name = params.get('tap_name', 'mytap0')
        delete_tap(tap_name)

    elif iface_type == "network":
        network_dict = eval(params.get("network_dict", "[]"))
        if network_dict:
            libvirt_network.create_or_del_network(network_dict, is_del=True)


def check_throughput(serv_runner, cli_runner, ip_addr, bw, th_type):
    """
    Check actual thoughput of network using netperf

    :param serv_runner: runner of netserver
    :param cli_runner: runner of netperf client
    :param ip_addr: ip address
    :param bw: bandwidth setting
    :param th_type: inbound or outbound
    """

    serv_runner('netserver')
    netperf_out = cli_runner(f'netperf -H {ip_addr}')
    LOG.debug(netperf_out)
    serv_runner('pkill netserver')

    actual_throu = float(netperf_out.strip().splitlines()[-1].split()[-1])
    expect_throu = int(bw) * 8 / 1024

    msg = f'Expected {th_type}: {expect_throu}, actual {th_type}: {actual_throu}'
    if not math.isclose(expect_throu, actual_throu, rel_tol=0.1):
        raise exceptions.TestFail(f'Actual {th_type} is not close to expected '
                                  f'{th_type}:\n{msg}')
    LOG.debug(msg)


def exec_netperf_test(params, env):
    """
    Verify the guest can work well under the netperf stress test

    :param params: Dictionary with the test parameters.
    :param env: Dictionary with test environment.
    """
    netperf_client = params.get("netperf_client")
    netperf_server = params.get("netperf_server")
    extra_cmd_opts = params.get("extra_cmd_opts", "")
    netperf_timeout = params.get("netperf_timeout", "60")
    test_protocol = params.get("test_protocol")
    vms = params.get('vms').split()
    vm_objs = {vm_i: env.get_vm(vm_i) for vm_i in vms}
    before_test_cores = process.run("coredumpctl list", ignore_status=True, verbose=True).stdout_text

    def _get_access_info(netperf_address):
        LOG.debug(f"check {netperf_address}...")
        session = None
        if re.match(r"((\d){1,3}\.){3}(\d){1,3}", netperf_address):
            func = process.run
            test_ip = netperf_address
        else:
            if netperf_address not in vms:
                raise exceptions.TestError(f"Unable to get {netperf_address} from {vms}!")
            vm = vm_objs.get(netperf_address)
            if not vm.is_alive():
                vm.start()
            session = vm.wait_for_login()
            test_ip = vm.get_address()
            func = session.cmd
        return func, test_ip, session

    c_func, c_ip, c_session = _get_access_info(netperf_client)
    s_func, s_ip, s_session = _get_access_info(netperf_server)

    try:
        if not utils_package.package_install("netperf", c_session):
            raise exceptions.TestError("Unable to install netperf in the client host!")
        if not utils_package.package_install("netperf", s_session):
            raise exceptions.TestError("Unable to install netperf in the server host!")
        c_func("systemctl stop firewalld")
        s_func("systemctl stop firewalld")

        LOG.debug("Start netserver...")
        if s_ip == netperf_server:
            s_func("killall netserver", ignore_status=True)
        s_func("netserver")

        LOG.debug("Run netperf command...")
        test_cmd = f"netperf -H {s_ip} -l {netperf_timeout} -C -c -t {test_protocol} {extra_cmd_opts}"
        c_func(test_cmd, timeout=120)

        for vm in vm_objs.values():
            try:
                vm.wait_for_login().close()
            except (remote.LoginError, aexpect.ShellError) as e:
                LOG.error(f"Unable to access to {vm.name}, guest os may have crashed - {e}")
                vm.destroy()
                vm.start()
                vm.wait_for_login().close()
        after_test_cores = process.run("coredumpctl list",
                                       ignore_status=True, verbose=True).stdout_text
        if after_test_cores != before_test_cores:
            raise exceptions.TestFail("There are coredump files during the test!")

    finally:
        LOG.info("Test teardown: Cleanup env.")
        s_func("killall netserver")
        s_func("systemctl start firewalld")
        # TODO: Start firewalld on guest
        process.run("systemctl start firewalld", ignore_status=True)


def prepare_single_vm(params, vm_name, disk_path='', iface_list=[]):
    """
    Prepare single vm with specified disk image and interface.

    :params vm_name: name of the vm.
    :params disk_path: path of the disk image.
    :params iface_list: list of interface for vm.
    """
    if vm_name not in virsh.dom_list().stdout_text:
        params.update(
            {"vm_name_bootc": vm_name,
             "vm_disk_image_path": disk_path,
             "iso_install_path": os.path.join(os.path.dirname(disk_path) + f"/{vm_name}.iso"),
             "firmware": "efi"
             })
        if "win" in vm_name:
            params.update({"disk_image_type": "anaconda-iso"})
        else:
            params.update({"disk_image_type": "qcow2"})
        bootc_image_build_utils.virt_install_vm(params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml.del_device('interface', by_tag=True)
    if iface_list:
        if not isinstance(iface_list, list):
            iface_list = [iface_list]
        for index, iface_attrs in enumerate(iface_list):
            libvirt_vmxml.modify_vm_device(vmxml, 'interface', iface_attrs, index=index)
    LOG.debug(f'Test with vm xml:\n{vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)}')


def prepare_vms_with_iface(params, vm_image_path=[], iface_list=[]):
    """
    Prepare several vms with specified disk image path and interface.

    :params vm_image_path: list of disk image path.
    :params iface_list: list of interface.
    """
    vm_list = []
    for image_path in vm_image_path:
        guest_version = os.path.basename(image_path)
        vm_name = "".join([re.findall("Win|RHEL", guest_version, re.IGNORECASE)[0], "_vm"])
        LOG.debug("TEST_LOOP: Test '%s' with guest version '%s'.", vm_name, guest_version)

        prepare_single_vm(params, vm_name, image_path, iface_list)
        vm_list.append(vm_name)
    return vm_list


def set_guest_iface_mtu(test, vm_session, iface_name='', mtu=1500):
    """
    Set guest interface MTU.

    :params vm_session: libvirt domain object session.
    :params iface_name: name of the interface.
    :params mtu: MTU value
    """
    if not iface_name:
        iface_name = utils_net.get_linux_ifname(session=vm_session)[0]

    cmd = 'sudo ifconfig {0} mtu {1} up'.format(iface_name, mtu)
    status, output = vm_session.cmd_status_output(cmd)
    if status:
        test.fail("Fail to set mtu on guest interface: %s." % output)
