"""
Test libvirt SPICE features.
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
from virttest.libvirt_xml import NetworkXML
from virttest.libvirt_xml import IPXML
from virttest import virsh
from virttest import virt_vm
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_config
from virttest import utils_libvirtd
from virttest.libvirt_xml.devices.graphics import Graphics


class Network(object):

    """
    Class to create a temporary network for testing.
    """

    def create_vnet_xml(self, address='192.168.123.1'):
        """
        Create XML for a virtual network.
        """
        if not self.address:
            raise Exception('')

        net_xml = NetworkXML()
        net_xml.name = self.name
        ip = IPXML(address=address)
        net_xml.ip = ip
        return address, net_xml

    def create_macvtap_xml(self):
        """
        Create XML for a macvtap network.
        """
        if not self.dev:
            raise Exception('')

        net_xml = NetworkXML()
        net_xml.name = self.name
        net_xml.forward = {'mode': 'bridge', 'dev': self.dev}
        ip = utils_net.get_ip_address_by_interface(self.dev)
        return ip, net_xml

    def create_bridge_xml(self):
        """
        Create XML for a bridged network.
        """
        if not self.dev:
            raise Exception('')

        net_xml = NetworkXML()
        net_xml.name = self.name

        net_xml.forward = {'mode': 'bridge'}
        net_xml.bridge = {'name': self.dev}
        ip = utils_net.get_ip_address_by_interface(self.dev)
        return ip, net_xml

    def __init__(self, net_type, dev='', address=''):
        self.name = 'virt-test-net'
        self.dev = dev
        self.address = address
        if net_type == 'vnet':
            self.ip, net_xml = self.create_vnet_xml()
        elif net_type == 'macvtap':
            self.ip, net_xml = self.create_macvtap_xml()
        elif net_type == 'bridge':
            self.ip, net_xml = self.create_bridge_xml()
        net_xml.create()

    def destroy(self):
        """
        Clear up created network.
        """
        return virsh.net_destroy(self.name)


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

    def listening_on(self, port, max_retry=30):
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
        spice_tls = params.get("spice_tls", "not_set")
        spice_listen = params.get("spice_listen", "not_set")
        x509_dir = params.get("x509_dir", "not_set")
        prepare_cert = params.get("prepare_cert", "yes")
        port_min = params.get("remote_display_port_min", 'not_set')
        port_max = params.get("remote_display_port_max", 'not_set')

        if x509_dir == 'not_set':
            self.x509_dir_real = '/etc/pki/libvirt-spice'
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
            del self.qemu_config.spice_tls_x509_cert_dir
        else:
            self.qemu_config.spice_tls_x509_cert_dir = x509_dir

        if spice_tls == 'not_set':
            del self.qemu_config.spice_tls
        else:
            self.qemu_config.spice_tls = spice_tls

        if port_min == 'not_set':
            del self.qemu_config.remote_display_port_min
        else:
            self.qemu_config.remote_display_port_min = port_min

        if port_max == 'not_set':
            del self.qemu_config.remote_display_port_max
        else:
            self.qemu_config.remote_display_port_max = port_max

        if spice_listen == 'not_set':
            del self.qemu_config.spice_listen
        elif spice_listen in ['valid_ipv4', 'valid_ipv6']:
            self.qemu_config.spice_listen = expected_ip
        else:
            self.qemu_config.spice_listen = spice_listen

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
    Backup original libvirt spice x509 certification directory.
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
    Restore original libvirt spice x509 certification directory
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
            logging.debug("Checking %s %s:%s", ip.iface, ip, port)
            if not ip.listening_on(port) and ip in expected_ips:
                raise error.TestFail(
                    'Expect listening on %s:%s but not.' % (ip, port))
            if ip.listening_on(port) and ip not in expected_ips:
                raise error.TestFail(
                    'Expect not listening on %s:%s but true.' % (ip, port))


def qemu_spice_options(libvirt_vm):
    """
    Get spice options from VM's qemu command line as dicts.
    """
    pid = libvirt_vm.get_pid()
    res = utils.run("ps -p %s -o cmd h" % pid)
    match = re.search(r'-spice\s*(\S*)', res.stdout)
    if match:
        spice_opt = match.groups()[0]
    spice_dict = {}
    plaintext_channels = set()
    tls_channels = set()
    for opt in spice_opt.split(','):
        if '=' in opt:
            key, value = opt.split('=')
            if key == 'plaintext-channel':
                plaintext_channels.add(value)
            elif key == 'tls-channel':
                tls_channels.add(value)
            else:
                spice_dict[key] = value
        else:
            spice_dict[opt] = True
    for key in spice_dict:
        logging.debug("%s: %s", key, spice_dict[key])
    return spice_dict, plaintext_channels, tls_channels


def get_expected_listen_ips(params, network, expected_result):
    """
    Predict listen IPs from parameters.
    """
    listen_type = params.get("listen_type", "not_set")

    def random_ip(version='ipv4'):
        """
        Randomly select a valid ip with target version.
        """
        ips = [ip for ip in get_all_ips() if ip.version == version]
        return random.choice(ips)

    if listen_type == 'network':
        expected_ips = [IPAddress(network.ip)]
    else:
        if listen_type == 'address':
            spice_listen = params.get("listen_address", 'not_set')
        else:
            spice_listen = params.get("spice_listen", "not_set")

        if spice_listen == 'not_set':
            expected_ips = [IPAddress('127.0.0.1')]
        elif spice_listen == 'valid_ipv4':
            expected_ips = [random_ip('ipv4')]
        elif spice_listen == 'valid_ipv6':
            expected_ips = [random_ip('ipv6')]
        else:
            listen_ip = IPAddress(spice_listen)
            logging.debug("Listen IP: %s", listen_ip)
            if listen_ip == IPAddress('0.0.0.0'):
                expected_ips = [ip for ip in get_all_ips()
                                if ip.version == 'ipv4']
            elif listen_ip == IPAddress('::'):
                expected_ips = [ip for ip in get_all_ips()]
            else:
                expected_ips = [listen_ip]
        for ip in expected_ips:
            logging.debug("Expected IP: %s", ip)

    expected_result['ips'] = expected_ips


def get_expected_channels(params, expected_result):
    """
    Predict spice channels from parameters.
    """
    default_mode = params.get("defaultMode", "not_set")
    channels_str = params.get("channels", "")
    tls_channels = []
    plaintext_channels = []
    if default_mode == 'secure':
        tls_channels.append('default')
    elif default_mode == 'insecure':
        plaintext_channels.append('default')

    if channels_str:
        for channel_str in channels_str.strip().split(','):
            name, mode = channel_str.split(':')
            if mode == 'secure':
                tls_channels.append(name)
            elif mode == 'insecure':
                plaintext_channels.append(name)

    logging.debug("tls_channels: %s", tls_channels)
    logging.debug("plaintext_channels: %s", plaintext_channels)

    expected_result['tls_channels'] = tls_channels
    expected_result['plaintext_channels'] = plaintext_channels


def get_expected_ports(params, expected_result):
    """
    Predict listening ports from parameters.
    """
    port = params.get("port", "not_set")
    tls_port = params.get("tlsPort", "not_set")
    autoport = params.get("autoport", "yes")
    default_mode = params.get("defaultMode", "any")
    port_min = params.get("remote_display_port_min", '5900')
    port_max = params.get("remote_display_port_max", '65535')

    plaintext_channels = expected_result['plaintext_channels']
    tls_channels = expected_result['tls_channels']
    expected_ips = expected_result['ips']

    port_allocator = PortAllocator(expected_ips, port_min, port_max)

    if port == '0':
        port = 'not_set'
    if tls_port == '0':
        tls_port = 'not_set'

    expected_port = port
    expected_tls_port = tls_port

    logging.debug("port: %s", port)
    logging.debug("tls-port: %s", tls_port)

    insecure_autoport = port == '-1' or autoport == 'yes'
    secure_autoport = tls_port == '-1' or autoport == 'yes'

    if insecure_autoport:
        if plaintext_channels or default_mode == 'any':
            expected_port = port_allocator.allocate()
        else:
            expected_port = 'not_set'

    if secure_autoport:
        if tls_channels or default_mode == 'any':
            expected_tls_port = port_allocator.allocate()
        else:
            expected_tls_port = 'not_set'

    if expected_port == expected_tls_port == 'not_set':
        if insecure_autoport:
            expected_port = port_allocator.allocate()
        if secure_autoport:
            expected_tls_port = port_allocator.allocate()

    if expected_port != 'not_set' and int(expected_port) < -1:
        expected_port = 'not_set'
    if expected_tls_port != 'not_set' and int(expected_tls_port) < -1:
        expected_tls_port = 'not_set'

    logging.debug('expected_port: ' + expected_port)
    logging.debug('expected_tls_port: ' + expected_tls_port)

    expected_result['port'] = expected_port
    expected_result['tls_port'] = expected_tls_port


def get_fail_pattern(params, expected_result):
    """
    Predict patterns of failure messages from parameters.
    """
    prepare_cert = params.get("prepare_cert", "yes")
    spice_tls = params.get("spice_tls", "not_set")
    default_mode = params.get("defaultMode", "any")

    plaintext_channels = expected_result['plaintext_channels']
    tls_channels = expected_result['tls_channels']
    expected_ips = expected_result['ips']
    expected_port = expected_result['port']
    expected_tls_port = expected_result['tls_port']

    tls_fail_patts = []

    if expected_tls_port == 'not_set':
        if tls_channels:
            tls_fail_patts.append(r'TLS port( is)? not provided')
    else:
        if spice_tls != '1':
            tls_fail_patts.append(r'TLS is disabled')

    plaintext_fail_patts = []
    if expected_port == 'not_set':
        if plaintext_channels:
            plaintext_fail_patts.append(r'plain port( is)? not provided')

    fail_patts = []
    if default_mode == 'any':
        if tls_fail_patts and plaintext_fail_patts:
            fail_patts += tls_fail_patts + plaintext_fail_patts
        elif tls_fail_patts and not plaintext_fail_patts:
            if tls_channels:
                fail_patts += tls_fail_patts
            else:
                expected_tls_port = expected_result['tls_port'] = 'not_set'
        elif not tls_fail_patts and plaintext_fail_patts:
            if plaintext_channels:
                fail_patts += plaintext_fail_patts
            else:
                # This line won't be reached currently.
                # It's here for consistency and clearity.
                expected_port = expected_result['port'] = 'not_set'
    else:
        fail_patts += tls_fail_patts + plaintext_fail_patts

    if expected_port == expected_tls_port:
        if expected_port == 'not_set':
            fail_patts.append(r'neither port nor tls-port specified')
            fail_patts.append(r'port is out of range')
        else:
            if 0 <= int(expected_port) < 1024:
                fail_patts.append(r'binding socket to \S* failed')
            elif int(expected_port) > 65535 or int(expected_port) < -1:
                fail_patts.append(r'port is out of range')
            else:
                fail_patts.append(r'Failed to reserve port')
                fail_patts.append(r'binding socket to \S* failed')

    spice_fail_patts = []
    if expected_tls_port != 'not_set' and not fail_patts:
        if prepare_cert == 'no':
            spice_fail_patts.append(r'Could not load certificates')
        if 0 <= int(expected_tls_port) < 1024:
            spice_fail_patts.append(r'binding socket to \S* failed')
        elif int(expected_tls_port) > 65535 or int(expected_tls_port) < -1:
            spice_fail_patts.append(r'port is out of range')

    if expected_port != 'not_set' and not fail_patts:
        if 0 <= int(expected_port) < 1024:
            spice_fail_patts.append(r'binding socket to \S* failed')
        elif int(expected_port) > 65535 or int(expected_port) < -1:
            spice_fail_patts.append(r'port is out of range')
    fail_patts += spice_fail_patts

    if fail_patts or expected_port == 'not_set':
        fail_patts += tls_fail_patts

    if any([ip not in get_all_ips() for ip in expected_ips]):
        fail_patts.append(r'binding socket to \S* failed')

    expected_result['fail_patts'] = fail_patts


def get_expected_spice_options(params, network, expected_result):
    """
    Predict qemu spice options from parameters.
    """
    x509_dir = params.get("x509_dir", "/etc/pki/libvirt-spice")
    image_compression = params.get("image_compression", "not_set")
    jpeg_compression = params.get("jpeg_compression", "not_set")
    zlib_compression = params.get("zlib_compression", "not_set")
    playback_compression = params.get("playback_compression", "not_set")
    listen_type = params.get("listen_type", "not_set")

    expected_port = expected_result['port']
    expected_tls_port = expected_result['tls_port']

    if listen_type == 'network':
        listen_address = network.ip
    else:
        if listen_type == 'address':
            spice_listen = params.get("listen_address", "127.0.0.1")
        else:
            spice_listen = params.get("spice_listen", "127.0.0.1")

        if spice_listen in ['valid_ipv4', 'valid_ipv6']:
            listen_address = str(expected_result['ips'][0])
        else:
            listen_address = spice_listen

    expected_opts = {
        'addr': listen_address,
        'disable-ticketing': True,
        'seamless-migration': 'on',
    }

    if expected_port != 'not_set':
        expected_opts['port'] = expected_port
    if expected_tls_port != 'not_set':
        expected_opts['tls-port'] = expected_tls_port
        expected_opts['x509-dir'] = x509_dir

    if image_compression != 'not_set':
        expected_opts['image-compression'] = image_compression
    if jpeg_compression != 'not_set':
        expected_opts['jpeg-wan-compression'] = jpeg_compression
    if zlib_compression != 'not_set':
        expected_opts['zlib-glz-wan-compression'] = zlib_compression
    if playback_compression != 'not_set':
        expected_opts['playback-compression'] = playback_compression

    expected_result['options'] = expected_opts


def get_expected_results(params, network):
    """
    Predict all expected results from parameters.
    """

    expected_result = {}

    get_expected_channels(params, expected_result)
    get_expected_listen_ips(params, network, expected_result)
    try:
        get_expected_ports(params, expected_result)
    except ValueError:
        expected_result['fail_patts'] = ['Unable to find an unused port']
    else:
        get_fail_pattern(params, expected_result)
        get_expected_spice_options(params, network, expected_result)

    return expected_result


def check_result(spice_opts, opt_plaintext_channels, opt_tls_channels,
                 expected_result, all_ips):
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
            raise error.TestFail("Expect qemu spice options is %s, but get %s"
                                 % (exp_opts, opts))
        else:
            if type(opts) == dict:
                for key in exp_opts:
                    if opts[key] != exp_opts[key]:
                        logging.debug("Key %s expected %s, but got %s",
                                      key, exp_opts[key], opts[key])
                        raise error.TestFail(
                            "Expect qemu spice options is %s, but get %s"
                            % (exp_opts, opts))

    expected_ips = expected_result['ips']

    compare_opts(spice_opts, expected_result['options'])
    compare_opts(opt_plaintext_channels, expected_result['plaintext_channels'])
    compare_opts(opt_tls_channels, expected_result['tls_channels'])

    expected_ports = [expected_result['port'], expected_result['tls_port']]
    expected_ports = [p for p in expected_ports if p != 'not_set']
    check_addr_port(all_ips, expected_ips, expected_ports)


def generate_graphic_xml(params, expected_result):
    """
    Generate graphics XML using input parameters.
    """
    autoport = params.get("autoport", "yes")
    default_mode = params.get("defaultMode", "not_set")
    channels_str = params.get("channels", "")
    port = params.get("port", "not_set")
    tls_port = params.get("tlsPort", "not_set")
    image_compression = params.get("image_compression", "not_set")
    jpeg_compression = params.get("jpeg_compression", "not_set")
    zlib_compression = params.get("zlib_compression", "not_set")
    playback_compression = params.get("playback_compression", "not_set")
    listen_type = params.get("listen_type", "not_set")

    graphic = Graphics(type_name='spice')

    if autoport != 'not_set':
        graphic.autoport = autoport

    if port != 'not_set':
        graphic.port = port

    if tls_port != 'not_set':
        graphic.tlsPort = tls_port

    if default_mode != 'not_set':
        graphic.defaultMode = default_mode

    if image_compression != 'not_set':
        graphic.image_compression = image_compression

    if jpeg_compression != 'not_set':
        graphic.jpeg_compression = jpeg_compression

    if zlib_compression != 'not_set':
        graphic.zlib_compression = zlib_compression

    if playback_compression != 'not_set':
        graphic.playback_compression = playback_compression

    channels = []
    if channels_str:
        for channel_str in channels_str.strip().split(','):
            name, mode = channel_str.split(':')
            channels.append({'name': name, 'mode': mode})
    graphic.channel = channels

    if listen_type == 'network':
        listen = {'type': 'network', 'network': 'virt-test-net'}
        graphic.listens = [listen]
    elif listen_type == 'address':
        address = params.get("listen_address", "127.0.0.1")
        if address in ['valid_ipv4', 'valid_ipv6']:
            address = str(expected_result['ips'][0])
        listen = {'type': 'address', 'address': address}
        graphic.listens = [listen]

    return graphic


def setup_network(params):
    """
    Create network according to parameters. return Network instance if
    succeeded or None if no network need to be created.
    """
    listen_type = params.get("listen_type", "not_set")

    if listen_type == 'network':
        net_type = params.get("network_type", "")

        if not net_type:
            raise error.TestError(
                "listen_type is network but network_type is not set.")

        address = params.get("vnet_address", "")

        device = ''
        if net_type == 'macvtap':
            device = params.get("macvtap_device", "")
        elif net_type == 'bridge':
            device = params.get("bridge_device", "")

        if 'EXAMPLE' in device:
            raise error.TestNAError(
                'A device name should be manually assigned')

        return Network(net_type, dev=device, address=address)


def run(test, params, env):
    """
    Test of libvirt SPICE related features.

    1) Block specified ports if required;
    2) Setup SPICE TLS certification if required;
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

    network = setup_network(params)

    expected_result = get_expected_results(params, network)
    env_state = EnvState(params, str(expected_result['ips'][0]))

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        vm_xml.remove_all_graphics()
        graphic = generate_graphic_xml(params, expected_result)
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

        spice_opts, plaintext_channels, tls_channels = qemu_spice_options(vm)
        check_result(spice_opts, plaintext_channels, tls_channels,
                     expected_result, all_ips)

        logging.debug("Test Succeed!")
    finally:
        for sock in sockets:
            sock.close()
        if network is not None:
            network.destroy()
        vm_xml_backup.sync()
        os.system('rm -rf /dev/shm/spice*')
        env_state.restore()
