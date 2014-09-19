"""
Test libvirt VNC features.
"""
import re
import os
import time
import random
import socket
import shutil
import logging
from autotest.client.shared import error
from autotest.client.shared import utils
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import virt_vm
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_config
from virttest import utils_libvirtd
from virttest.libvirt_xml.devices.graphics import Graphics


class IPAddress(object):

    """
    Class to manipulate IPv4 or IPv6 address.
    """
    addr = ''
    iface = ''
    scope = 0
    packed_addr = None

    def __init__(self, ip_str='', info=''):
        if info:
            try:
                self.iface = info['iface']
                self.addr = info['addr']
                self.version = info['version']
                self.scope = info['scope']
            except KeyError:
                pass

        if ip_str:
            self.canonicalize(ip_str)

    def __str__(self):
        if self.version == 'ipv6':
            return "%s%%%s" % (self.addr, self.scope)
        else:
            return self.addr

    def canonicalize(self, ip_str):
        """
        Parse an IP string for listen to IPAddress content.
        """
        try:
            if ':' in ip_str:
                self.version = 'ipv6'
                if '%' in ip_str:
                    ip_str, scope = ip_str.split('%')
                    self.scope = int(scope)
                self.packed_addr = socket.inet_pton(socket.AF_INET6, ip_str)
                self.addr = socket.inet_ntop(socket.AF_INET6, self.packed_addr)
            else:
                self.version = 'ipv4'
                self.packed_addr = socket.inet_pton(socket.AF_INET, ip_str)
                self.addr = socket.inet_ntop(socket.AF_INET, self.packed_addr)
        except socket.error, detail:
            if 'illegal IP address' in str(detail):
                self.addr = ip_str
                self.version = 'hostname'

    def listening_on(self, port, max_retry=100):
        """
        Check whether a port is used for listening.
        """

        def test_connection(self, port):
            """
            Try connect to a port and return the connect result as error no.
            """
            port = int(port)
            if self.version == 'ipv6':
                sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                result = sock.connect_ex((self.addr, port, 0, self.scope))
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                result = sock.connect_ex((self.addr, port))
            return result

        retry = 0
        while True:
            if retry == max_retry:
                return False

            result = test_connection(self, port)
            logging.debug('Connect result: %s', result)

            if result == 0:
                return True
            elif result == 11:  # Resource temporarily unavailable.
                time.sleep(0.1)
                retry += 1
            else:
                return False

    def __eq__(self, other_ip):
        if self.version != other_ip.version:
            return False
        if self.addr != other_ip.addr:
            return False
        if self.iface and other_ip.iface and self.iface != other_ip.iface:
            return False
        return True


class PortAllocator(object):

    """
    Predict automatically allocated port by libvirt.
    """
    ports = set()
    ips = []
    port_min = 0
    port_max = 65535

    def __init__(self, ips, port_min=0, port_max=65535,):
        self.port_min = int(port_min)
        self.port_max = int(port_max)
        self.ips = ips

    def allocate(self):
        """
        Predict automatically allocated port by libvirt.
        """
        port = self.port_min
        while True:
            if port in self.ports or any(
                    ip.listening_on(port)
                    for ip in self.ips):
                port += 1
            else:
                break
        if port > self.port_max:
            raise ValueError('Port allocator overflow: %s > %s.'
                             % (port, self.port_max))
        self.ports.add(port)
        return str(port)


class EnvState(object):

    """
    Prepare environment state for test according to input parameters,
    and recover it after test.
    """
    sockets = []
    libvirtd = utils_libvirtd.Libvirtd()
    qemu_config = utils_config.LibvirtQemuConfig()
    x509_dir_real = ""
    x509_dir_bak = ""

    def __init__(self, params, expected_ip):
        vnc_tls = params.get("vnc_tls", "not_set")
        vnc_listen = params.get("vnc_listen", "not_set")
        x509_dir = params.get("x509_dir", "not_set")
        prepare_cert = params.get("prepare_cert", "yes")
        port_min = params.get("remote_display_port_min", 'not_set')
        port_max = params.get("remote_display_port_max", 'not_set')
        auto_unix_socket = params.get("vnc_auto_unix_socket", 'not_set')
        tls_x509_verify = params.get("vnc_tls_x509_verify", 'not_set')

        if x509_dir == 'not_set':
            self.x509_dir_real = '/etc/pki/libvirt-vnc'
        else:
            self.x509_dir_real = x509_dir

        self.x509_dir_bak = _backup_x509_dir(self.x509_dir_real)
        if prepare_cert == 'yes':
            utils_misc.create_x509_dir(
                self.x509_dir_real,
                '/C=NC/L=Raleigh/O=Red Hat/CN=virt-test',
                '/C=NC/L=Raleigh/O=Red Hat/CN=virt-test',
                'none', True)

        if x509_dir == 'not_set':
            del self.qemu_config.vnc_tls_x509_cert_dir
        else:
            self.qemu_config.vnc_tls_x509_cert_dir = x509_dir

        if vnc_tls == 'not_set':
            del self.qemu_config.vnc_tls
        else:
            self.qemu_config.vnc_tls = vnc_tls

        if port_min == 'not_set':
            del self.qemu_config.remote_display_port_min
        else:
            self.qemu_config.remote_display_port_min = port_min

        if port_max == 'not_set':
            del self.qemu_config.remote_display_port_max
        else:
            self.qemu_config.remote_display_port_max = port_max

        if auto_unix_socket == 'not_set':
            del self.qemu_config.vnc_auto_unix_socket
        else:
            self.qemu_config.vnc_auto_unix_socket = auto_unix_socket

        if tls_x509_verify == 'not_set':
            del self.qemu_config.vnc_tls_x509_verify
        else:
            self.qemu_config.vnc_tls_x509_verify = tls_x509_verify

        if vnc_listen == 'not_set':
            del self.qemu_config.vnc_listen
        elif vnc_listen in ['valid_ipv4', 'valid_ipv6']:
            self.qemu_config.vnc_listen = expected_ip
        else:
            self.qemu_config.vnc_listen = vnc_listen

        self.libvirtd.restart()

    def restore(self):
        """
        Recover environment state after test.
        """
        _restore_x509_dir(self.x509_dir_real, self.x509_dir_bak)
        self.qemu_config.restore()
        self.libvirtd.restart()


def _backup_x509_dir(path):
    """
    Backup original libvirt VNC x509 certification directory.
    """
    if os.path.isdir(path):
        backup_path = path + '.bak'
        if os.path.isdir(backup_path):
            shutil.rmtree(backup_path)
        shutil.move(path, backup_path)
        return backup_path
    else:
        return ""


def _restore_x509_dir(path, backup_path):
    """
    Restore original libvirt VNC x509 certification directory
    from backup_path.
    """
    if os.path.isdir(path):
        shutil.rmtree(path)
    if backup_path:
        shutil.move(backup_path, path)


def get_all_ips():
    """
    Get all IPv4 and IPv6 addresses from all interfaces.
    """
    ips = []

    # Get all ipv4 IPs.
    for iface in utils_net.get_host_iface():
        try:
            ip_addr = utils_net.get_ip_address_by_interface(iface)
        except utils_net.NetError:
            pass
        else:
            if ip_addr is not None:
                ip_info = {
                    'iface': iface,
                    'addr': ip_addr,
                    'version': 'ipv4',
                }
                ips.append(IPAddress(info=ip_info))

    # Get all ipv6 IPs.
    if_inet6_fp = open('/proc/net/if_inet6', 'r')
    for line in if_inet6_fp.readlines():
        # ipv6_ip, dev_no, len_prefix, scope, iface_flag, iface
        ipv6_ip, dev_no, _, _, _, iface = line.split()
        ipv6_ip = ":".join([ipv6_ip[i:i + 4] for i in range(0, 32, 4)])
        ip_info = {
            'iface': iface,
            'addr': ipv6_ip,
            'version': 'ipv6',
            'scope': int(dev_no, 16)
        }
        ips.append(IPAddress(info=ip_info))
    if_inet6_fp.close()

    return ips


def check_addr_port(all_ips, expected_ips, ports):
    """
    Check whether current listening IPs and ports consists with prediction.
    """
    for ip in all_ips:
        for port in ports:
            logging.error("Checking %s %s:%s", ip.iface, ip, port)
            if not ip.listening_on(port) and ip in expected_ips:
                raise error.TestFail(
                    'Expect listening on %s:%s but not.' % (ip, port))
            if ip.listening_on(port) and ip not in expected_ips:
                raise error.TestFail(
                    'Expect not listening on %s:%s but true.' % (ip, port))


def qemu_vnc_options(libvirt_vm):
    """
    Get VNC options from VM's qemu command line as dicts.
    """
    pid = libvirt_vm.get_pid()
    res = utils.run("ps -p %s -o cmd h" % pid)
    match = re.search(r'-vnc\s*(\S*)', res.stdout)
    if match:
        vnc_opt = match.groups()[0]
    logging.error('Qemu VNC option is: %s', vnc_opt)
    vnc_dict = {}

    vnc_opt_split = vnc_opt.split(',', 1)

    vnc_addr_port = vnc_opt_split[0]
    addr, vnc_dict['port'] = vnc_addr_port.rsplit(':', 1)
    if addr.startswith('[') and addr.endswith(']'):
        addr = addr[1:-1]
    vnc_dict['addr'] = addr

    if len(vnc_opt_split) == 2:
        vnc_opt = vnc_opt_split[1]
        for opt in vnc_opt.split(','):
            if '=' in opt:
                key, value = opt.split('=')
                vnc_dict[key] = value
            else:
                vnc_dict[opt] = True
    for key in vnc_dict:
        logging.debug("%s: %s", key, vnc_dict[key])
    return vnc_dict


def get_expected_listen_ips(params, expected_result):
    """
    Predict listen IPs from parameters.
    """

    def random_ip(version='ipv4'):
        """
        Randomly select a valid ip with target version.
        """
        ips = [ip for ip in get_all_ips() if ip.version == version]
        return random.choice(ips)

    vnc_listen = params.get("vnc_listen", "not_set")

    if vnc_listen == 'not_set':
        expected_ips = [IPAddress('127.0.0.1')]
    elif vnc_listen == 'valid_ipv4':
        expected_ips = [random_ip('ipv4')]
    elif vnc_listen == 'valid_ipv6':
        expected_ips = [random_ip('ipv6')]
    else:
        listen_ip = IPAddress(vnc_listen)
        logging.debug("Listen IP: %s", listen_ip)
        if listen_ip == IPAddress('0.0.0.0'):
            expected_ips = [ip for ip in get_all_ips() if ip.version == 'ipv4']
        elif listen_ip == IPAddress('::'):
            expected_ips = [ip for ip in get_all_ips()]
        else:
            expected_ips = [listen_ip]
    for ip in expected_ips:
        logging.debug("Expected IP: %s", ip)

    expected_result['ips'] = expected_ips


def get_expected_ports(params, expected_result):
    """
    Predict listening ports from parameters.
    """
    port = params.get("port", "not_set")
    autoport = params.get("autoport", "yes")
    port_min = params.get("remote_display_port_min", '5900')
    port_max = params.get("remote_display_port_max", '65535')
    auto_unix_socket = params.get("vnc_auto_unix_socket", 'not_set')

    expected_ips = expected_result['ips']

    if any([ip not in get_all_ips() for ip in expected_ips]):
        expected_result['port'] = 'not_set'
        return

    port_allocator = PortAllocator(expected_ips, port_min, port_max)

    expected_tcp_port = port

    autoport = port == '-1' or autoport == 'yes'

    if autoport:
        expected_tcp_port = port_allocator.allocate()

    if auto_unix_socket == '1':
        expected_tcp_port = 'not_set'

    logging.error('expected_tcp_port: ' + expected_tcp_port)

    expected_result['port'] = expected_tcp_port


def get_fail_pattern(params, expected_result):
    """
    Predict patterns of failure messages from parameters.
    """
    prepare_cert = params.get("prepare_cert", "yes")
    vnc_tls = params.get("vnc_tls", "not_set")
    expected_ips = expected_result['ips']
    expected_tcp_port = expected_result['port']

    fail_patts = []

    if vnc_tls == '1' and prepare_cert == 'no':
        fail_patts.append('Failed to find x509 certificates')

    if expected_tcp_port != 'not_set':
        if int(expected_tcp_port) - 5900 < 0:
            fail_patts.append('Failed to start VNC server')
        elif int(expected_tcp_port) > 65535:
            fail_patts.append('Failed to start VNC server')

    if any([ip not in get_all_ips() for ip in expected_ips]):
        fail_patts.append(r'Cannot assign requested address')

    expected_result['fail_patts'] = fail_patts


def get_expected_vnc_options(params, expected_result):
    """
    Predict qemu VNC options from parameters.
    """
    vm_name = params.get("main_vm", "virt-tests-vm1")
    x509_dir = params.get("x509_dir", "/etc/pki/libvirt-vnc")
    vnc_listen = params.get("vnc_listen", "127.0.0.1")
    vnc_tls = params.get("vnc_tls", "0")
    auto_unix_socket = params.get("vnc_auto_unix_socket", 'not_set')
    tls_x509_verify = params.get("vnc_tls_x509_verify", 'not_set')

    expected_port = expected_result['port']

    if vnc_listen in ['valid_ipv4', 'valid_ipv6']:
        vnc_listen = str(expected_result['ips'][0])
    else:
        vnc_listen = vnc_listen

    if auto_unix_socket == '1':
        addr = 'unix'
        port = '/var/lib/libvirt/qemu/%s.vnc' % vm_name
    else:
        addr = vnc_listen
        if expected_port != 'not_set':
            port = str(int(expected_port) - 5900)
        else:
            port = '0'

    expected_opts = {
        'addr': addr,
        'port': port,
    }

    if vnc_tls == '1':
        expected_opts['tls'] = True
        if tls_x509_verify == '1':
            expected_opts['x509verify'] = x509_dir
        else:
            expected_opts['x509'] = x509_dir

    expected_result['options'] = expected_opts


def get_expected_results(params):
    """
    Predict all expected results from parameters.
    """

    expected_result = {}

    get_expected_listen_ips(params, expected_result)
    try:
        get_expected_ports(params, expected_result)
    except ValueError:
        expected_result['fail_patts'] = ['Unable to find an unused port']
    else:
        get_fail_pattern(params, expected_result)
        get_expected_vnc_options(params, expected_result)

    return expected_result


def check_result(vnc_opts, expected_result, all_ips):
    """
    Check test results by comparison with expected results.
    """
    def compare_opts(opts, exp_opts):
        """
        Compare two containers.
        """
        created = set(opts) - set(exp_opts)
        deleted = set(exp_opts) - set(opts)
        if len(created) or len(deleted):
            logging.debug("Created: %s", created)
            logging.debug("Deleted: %s", deleted)
            raise error.TestFail("Expect qemu VNC options is %s, but get %s"
                                 % (exp_opts, opts))
        else:
            if type(opts) == dict:
                for key in exp_opts:
                    if opts[key] != exp_opts[key]:
                        logging.debug("Key %s expected %s, but got %s",
                                      key, exp_opts[key], opts[key])
                        raise error.TestFail(
                            "Expect qemu VNC options is %s, but get %s"
                            % (exp_opts, opts))

    expected_ips = expected_result['ips']

    compare_opts(vnc_opts, expected_result['options'])

    expected_ports = [expected_result['port']]
    expected_ports = [p for p in expected_ports if p != 'not_set']
    check_addr_port(all_ips, expected_ips, expected_ports)


def generate_graphic_xml(params):
    """
    Generate graphics XML using input parameters.
    """
    autoport = params.get("autoport", "yes")
    port = params.get("port", "not_set")

    graphic = Graphics(type_name='vnc')

    if autoport != 'not_set':
        graphic.autoport = autoport

    if port != 'not_set':
        graphic.port = port

    return graphic


def run(test, params, env):
    """
    Test of libvirt VNC related features.

    1) Block specified ports if required;
    2) Setup VNC TLS certification if required;
    3) Setup graphics tag in VM;
    4) Try to start VM;
    5) Parse and check result with expected.
    6) Clean up environment.
    """

    vm_name = params.get("main_vm", "virt-tests-vm1")
    block_port = params.get("block_port", None)

    # Block specified ports
    sockets = []
    if block_port is not None:
        block_ports = [int(p) for p in block_port.split(',')]
        for port in block_ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', port))
            sock.listen(5)
            sockets.append(sock)

    expected_result = get_expected_results(params)
    env_state = EnvState(params, str(expected_result['ips'][0]))

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        vm_xml.remove_all_graphics()
        graphic = generate_graphic_xml(params)
        vm_xml.devices = vm_xml.devices.append(graphic)
        vm_xml.sync()
        all_ips = get_all_ips()

        fail_patts = expected_result['fail_patts']
        try:
            vm.start()
        except virt_vm.VMStartError, detail:
            if not fail_patts:
                raise error.TestFail(
                    "Expect VM can be started, but failed with: %s" % detail)
            for patt in fail_patts:
                if re.search(patt, str(detail)):
                    return
            raise error.TestFail(
                "Expect fail with error in %s, but failed with: %s"
                % (fail_patts, detail))
        else:
            if fail_patts:
                raise error.TestFail(
                    "Expect VM can't be started with %s, but started."
                    % fail_patts)

        vnc_opts = qemu_vnc_options(vm)
        check_result(vnc_opts, expected_result, all_ips)

        logging.debug("Test Succeed!")
    finally:
        for sock in sockets:
            sock.close()
        vm_xml_backup.sync()
        env_state.restore()
