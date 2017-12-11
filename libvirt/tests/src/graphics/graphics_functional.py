"""
Test libvirt SPICE and VNC features.
"""
import re
import os
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
from virttest.utils_test.libvirt import LibvirtNetwork
from virttest.libvirt_xml.devices.graphics import Graphics

from provider import libvirt_version


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
    spice_x509_dir_real = ""
    spice_x509_dir_bak = ""
    vnc_x509_dir_real = ""
    vnc_x509_dir_bak = ""

    def _backup_dir(self, path):
        """
        Backup original libvirt spice or vnc x509 certification directory.
        """
        if os.path.isdir(path):
            backup_path = path + '.bak'
            if os.path.isdir(backup_path):
                shutil.rmtree(backup_path)
            shutil.move(path, backup_path)
            return backup_path
        else:
            return ""

    def _restore_dir(self, path, backup_path):
        """
        Restore original libvirt spice or vnc x509 certification directory
        from backup_path.
        """
        if os.path.isdir(path):
            shutil.rmtree(path)
        if backup_path:
            shutil.move(backup_path, path)

    def __init__(self, params, expected_result):
        spice_tls = params.get("spice_tls", "not_set")
        spice_listen = params.get("spice_listen", "not_set")
        vnc_tls = params.get("vnc_tls", "not_set")
        vnc_listen = params.get("vnc_listen", "not_set")
        spice_x509_dir = params.get("spice_x509_dir", "not_set")
        vnc_x509_dir = params.get("vnc_x509_dir", "not_set")
        spice_prepare_cert = params.get("spice_prepare_cert", "yes")
        vnc_prepare_cert = params.get("vnc_prepare_cert", "yes")
        port_min = params.get("remote_display_port_min", 'not_set')
        port_max = params.get("remote_display_port_max", 'not_set')
        auto_unix_socket = params.get("vnc_auto_unix_socket", 'not_set')
        tls_x509_verify = params.get("vnc_tls_x509_verify", 'not_set')

        if spice_x509_dir == 'not_set':
            self.spice_x509_dir_real = '/etc/pki/libvirt-spice'
        else:
            self.spice_x509_dir_real = spice_x509_dir

        self.spice_x509_dir_bak = self._backup_dir(self.spice_x509_dir_real)
        if spice_prepare_cert == 'yes':
            utils_misc.create_x509_dir(
                self.spice_x509_dir_real,
                '/C=NC/L=Raleigh/O=Red Hat/CN=virt-test',
                '/C=NC/L=Raleigh/O=Red Hat/CN=virt-test',
                'none', True)

        if vnc_x509_dir == 'not_set':
            self.vnc_x509_dir_real = '/etc/pki/libvirt-vnc'
        else:
            self.vnc_x509_dir_real = vnc_x509_dir

        self.vnc_x509_dir_bak = self._backup_dir(self.vnc_x509_dir_real)
        if vnc_prepare_cert == 'yes':
            utils_misc.create_x509_dir(
                self.vnc_x509_dir_real,
                '/C=NC/L=Raleigh/O=Red Hat/CN=virt-test',
                '/C=NC/L=Raleigh/O=Red Hat/CN=virt-test',
                'none', True)

        if spice_x509_dir == 'not_set':
            del self.qemu_config.spice_tls_x509_cert_dir
        else:
            self.qemu_config.spice_tls_x509_cert_dir = spice_x509_dir

        if vnc_x509_dir == 'not_set':
            del self.qemu_config.vnc_tls_x509_cert_dir
        else:
            self.qemu_config.vnc_tls_x509_cert_dir = vnc_x509_dir

        if spice_tls == 'not_set':
            del self.qemu_config.spice_tls
        else:
            self.qemu_config.spice_tls = spice_tls

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

        if spice_listen == 'not_set':
            del self.qemu_config.spice_listen
        elif spice_listen in ['valid_ipv4', 'valid_ipv6']:
            expected_ip = str(expected_result['spice_ips'][0])
            self.qemu_config.spice_listen = expected_ip
        else:
            self.qemu_config.spice_listen = spice_listen

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
            expected_ip = str(expected_result['vnc_ips'][0])
            self.qemu_config.vnc_listen = expected_ip
        else:
            self.qemu_config.vnc_listen = vnc_listen

        self.libvirtd.restart()

    def restore(self):
        """
        Recover environment state after test.
        """
        self._restore_dir(
            self.spice_x509_dir_real,
            self.spice_x509_dir_bak)
        self._restore_dir(
            self.vnc_x509_dir_real,
            self.vnc_x509_dir_bak)
        self.qemu_config.restore()
        self.libvirtd.restart()


def check_addr_port(all_ips, expected_ips, ports):
    """
    Check whether current listening IPs and ports consists with prediction.
    """
    for ip in all_ips:
        for port in ports:
            # port 0 means port not set. No need to check here.
            if int(port) == 0:
                continue
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
    logging.info('Qemu spice option is: %s', spice_opt)

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
            spice_dict[opt] = 'yes'
    for key in spice_dict:
        logging.debug("%s: %s", key, spice_dict[key])
    return spice_dict, plaintext_channels, tls_channels


def qemu_vnc_options(libvirt_vm):
    """
    Get VNC options from VM's qemu command line as dicts.
    """
    pid = libvirt_vm.get_pid()
    res = utils.run("ps -p %s -o cmd h" % pid)
    match = re.search(r'-vnc\s*(\S*)', res.stdout)
    if match:
        vnc_opt = match.groups()[0]
    logging.debug('Qemu VNC option is: %s', vnc_opt)
    vnc_dict = {}

    vnc_opt_split = vnc_opt.split(',', 1)

    vnc_addr_port = vnc_opt_split[0]
    addr, vnc_dict['port'] = vnc_addr_port.rsplit(':', 1)

    #The format of vnc relevant arguments in qemu command line varies
    #in different RHEL versions, see below:
    #In RHEL6.9, like '-vnc unix:/var/lib/libvirt/qemu/*.vnc'
    #In RHEL7.3, like '-vnc unix:/var/lib/libvirt/qemu/*vnc.sock'
    #In RHEL7.5, like '-vnc vnc=unix:/var/lib/libvirt/qemu/*vnc.sock'

    #Note, for RHEL7.5, 'vnc=' is added before regular arguments.
    #For compatibility, one more handling is added for this change.
    addr = addr.split('=')[1] if '=' in addr else addr

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
                vnc_dict[opt] = 'yes'
    for key in vnc_dict:
        logging.debug("%s: %s", key, vnc_dict[key])
    return vnc_dict


def get_expected_listen_ips(params, networks, expected_result):
    """
    Predict listen IPs from parameters.
    """
    def random_ip(version='ipv4'):
        """
        Randomly select a valid ip with target version.
        """
        ips = [ip for ip in utils_net.get_all_ips() if ip.version == version]
        return random.choice(ips)

    spice_listen_type = params.get("spice_listen_type", "not_set")
    if spice_listen_type == 'none':
        expected_spice_ips = []
    elif spice_listen_type == 'network':
        net_type = params.get("spice_network_type", "vnet")
        spice_network = networks[net_type]
        expected_spice_ips = [utils_net.IPAddress(spice_network.ip)]
    else:
        if spice_listen_type == 'address':
            spice_listen = params.get("spice_listen_address", 'not_set')
        else:
            spice_listen = params.get("spice_listen", "not_set")

        if spice_listen == 'not_set':
            expected_spice_ips = [utils_net.IPAddress('127.0.0.1')]
        elif spice_listen == 'valid_ipv4':
            expected_spice_ips = [random_ip('ipv4')]
        elif spice_listen == 'valid_ipv6':
            expected_spice_ips = [random_ip('ipv6')]
        else:
            listen_ip = utils_net.IPAddress(spice_listen)
            if listen_ip == utils_net.IPAddress('0.0.0.0'):
                expected_spice_ips = [ip for ip in utils_net.get_all_ips()
                                      if ip.version == 'ipv4']
            elif listen_ip == utils_net.IPAddress('::'):
                expected_spice_ips = [ip for ip in utils_net.get_all_ips()]
            else:
                expected_spice_ips = [listen_ip]
    for ip in expected_spice_ips:
        logging.debug("Expected SPICE IP: %s", ip)

    expected_result['spice_ips'] = expected_spice_ips

    vnc_listen_type = params.get("vnc_listen_type", "not_set")
    vnc_listen = params.get("vnc_listen", "not_set")
    if vnc_listen_type == 'none':
        expected_vnc_ips = []
    elif vnc_listen_type == 'network':
        net_type = params.get("vnc_network_type", "vnet")
        vnc_network = networks[net_type]
        expected_vnc_ips = [utils_net.IPAddress(vnc_network.ip)]
    else:
        if vnc_listen_type == 'address':
            vnc_listen = params.get("vnc_listen_address", 'not_set')
        else:
            vnc_listen = params.get("vnc_listen", "not_set")

        if vnc_listen == 'not_set':
            expected_vnc_ips = [utils_net.IPAddress('127.0.0.1')]
        elif vnc_listen == 'valid_ipv4':
            expected_vnc_ips = [random_ip('ipv4')]
        elif vnc_listen == 'valid_ipv6':
            expected_vnc_ips = [random_ip('ipv6')]
        else:
            listen_ip = utils_net.IPAddress(vnc_listen)
            if listen_ip == utils_net.IPAddress('0.0.0.0'):
                expected_vnc_ips = [ip for ip in utils_net.get_all_ips()
                                    if ip.version == 'ipv4']
            elif listen_ip == utils_net.IPAddress('::'):
                expected_vnc_ips = [ip for ip in utils_net.get_all_ips()]
            else:
                expected_vnc_ips = [listen_ip]
    for ip in expected_vnc_ips:
        logging.debug("Expected VNC IP: %s", ip)

    expected_result['vnc_ips'] = expected_vnc_ips


def get_expected_channels(params, expected_result):
    """
    Predict SPICE channels from parameters.
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
    spice_xml = params.get("spice_xml", "no") == 'yes'
    vnc_xml = params.get("vnc_xml", "no") == 'yes'
    port_min = params.get("remote_display_port_min", '5900')
    port_max = params.get("remote_display_port_max", '65535')

    expected_spice_ips = expected_result['spice_ips']
    expected_vnc_ips = expected_result['vnc_ips']
    expected_ips = expected_spice_ips + expected_vnc_ips

    port_allocator = PortAllocator(expected_ips, port_min, port_max)

    if spice_xml:
        spice_port = params.get("spice_port", "not_set")
        spice_tls_port = params.get("spice_tlsPort", "not_set")
        spice_autoport = params.get("spice_autoport", "yes")
        default_mode = params.get("defaultMode", "any")
        spice_listen_type = params.get("spice_listen_type", "not_set")
        is_negative = params.get("negative_test", "no") == 'yes'

        get_expected_channels(params, expected_result)

        plaintext_channels = expected_result['plaintext_channels']
        tls_channels = expected_result['tls_channels']

        if spice_port == '0':
            spice_port = 'not_set'
        if spice_tls_port == '0':
            spice_tls_port = 'not_set'

        expected_port = spice_port
        expected_tls_port = spice_tls_port

        insecure_autoport = spice_port == '-1' or spice_autoport == 'yes'
        secure_autoport = spice_tls_port == '-1' or spice_autoport == 'yes'

        if spice_listen_type == 'none':
            expected_port = '0'
        elif (not is_negative) and (expected_port != 'not_set' and
                                    int(expected_port) < -1):
            expected_port = 'not_set'
            expected_tls_port = 'not_set'
        else:
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

            if expected_tls_port != 'not_set' and int(expected_tls_port) < -1:
                expected_tls_port = 'not_set'

            if expected_port != 'not_set' and int(expected_port) < -1:
                if expected_tls_port != 'not_set':
                    expected_port = 'not_set'

        logging.debug('Expected SPICE port: %s', expected_port)
        logging.debug('Expected SPICE tls_port: %s', expected_tls_port)
        expected_result['spice_port'] = expected_port
        expected_result['spice_tls_port'] = expected_tls_port

    if vnc_xml:
        vnc_port = params.get("vnc_port", "not_set")
        vnc_autoport = params.get("vnc_autoport", "yes")
        auto_unix_socket = params.get("vnc_auto_unix_socket", 'not_set')
        vnc_listen_type = params.get("vnc_listen_type", "not_set")

        if vnc_listen_type == 'none':
            expected_vnc_port = 'not_set'
        elif any([ip not in utils_net.get_all_ips() for ip in expected_vnc_ips]):
            expected_vnc_port = 'not_set'
            expected_result['vnc_port'] = 'not_set'
        else:
            expected_vnc_port = vnc_port

            vnc_autoport = vnc_port == '-1' or vnc_autoport == 'yes'

            if vnc_autoport:
                expected_vnc_port = port_allocator.allocate()

            if auto_unix_socket == '1':
                expected_vnc_port = 'not_set'

        logging.debug('Expected VNC port: ' + expected_vnc_port)
        expected_result['vnc_port'] = expected_vnc_port


def get_fail_pattern(params, expected_result):
    """
    Predict patterns of failure messages from parameters.
    """
    spice_xml = params.get("spice_xml", "no") == 'yes'
    vnc_xml = params.get("vnc_xml", "no") == 'yes'
    is_negative = params.get("negative_test", "no") == 'yes'

    fail_patts = []
    if spice_xml:
        spice_prepare_cert = params.get("spice_prepare_cert", "yes")
        spice_tls = params.get("spice_tls", "not_set")
        default_mode = params.get("defaultMode", "any")
        listen_type = params.get("spice_listen_type", 'not_set')

        plaintext_channels = expected_result['plaintext_channels']
        tls_channels = expected_result['tls_channels']
        expected_spice_ips = expected_result['spice_ips']
        expected_spice_port = expected_result['spice_port']
        expected_spice_tls_port = expected_result['spice_tls_port']

        tls_fail_patts = []
        if expected_spice_tls_port == 'not_set':
            if tls_channels:
                tls_fail_patts.append(r'TLS port( is)? not provided')
                tls_fail_patts.append(r'TLS connection is not available')
        else:
            if spice_tls != '1':
                tls_fail_patts.append(r'TLS is disabled')

        plaintext_fail_patts = []
        if expected_spice_port == 'not_set':
            if plaintext_channels:
                plaintext_fail_patts.append(r'plain port( is)? not provided')
                plaintext_fail_patts.append(r'plaintext connection is not available')
        if default_mode == 'any':
            if tls_fail_patts and plaintext_fail_patts:
                fail_patts += tls_fail_patts + plaintext_fail_patts
            elif tls_fail_patts and not plaintext_fail_patts:
                if tls_channels:
                    fail_patts += tls_fail_patts
                else:
                    expected_spice_tls_port = expected_result['spice_tls_port'] = 'not_set'
            elif not tls_fail_patts and plaintext_fail_patts:
                if plaintext_channels:
                    fail_patts += plaintext_fail_patts
                else:
                    expected_spice_port = expected_result['spice_port'] = 'not_set'
        else:
            fail_patts += tls_fail_patts + plaintext_fail_patts

        if expected_spice_port == expected_spice_tls_port:
            if expected_spice_port != 'not_set':
                if 0 <= int(expected_spice_port) < 1024:
                    fail_patts.append(r'binding socket to \S* failed')
                elif is_negative and (int(expected_spice_port) > 65535 or
                                      int(expected_spice_port) < -1):
                    fail_patts.append(r'port is out of range')
                else:
                    fail_patts.append(r'Failed to reserve port')
                    fail_patts.append(r'binding socket to \S* failed')

        spice_fail_patts = []
        if expected_spice_tls_port != 'not_set' and not fail_patts:
            if spice_prepare_cert == 'no':
                spice_fail_patts.append(r'Could not load certificates')
            if 0 <= int(expected_spice_tls_port) < 1024:
                spice_fail_patts.append(r'binding socket to \S* failed')
            elif is_negative and (int(expected_spice_tls_port) > 65535 or
                                  int(expected_spice_tls_port) < -1):
                spice_fail_patts.append(r'port is out of range')

        if expected_spice_port != 'not_set' and not fail_patts:
            if 0 <= int(expected_spice_port) < 1024 and listen_type != 'none':
                spice_fail_patts.append(r'binding socket to \S* failed')
            elif is_negative and (int(expected_spice_port) > 65535 or
                                  int(expected_spice_port) < -1):
                spice_fail_patts.append(r'port is out of range')
        fail_patts += spice_fail_patts

        if fail_patts or expected_spice_port == 'not_set':
            fail_patts += tls_fail_patts

        for ip in expected_spice_ips:
            if ip is not None and ip not in utils_net.get_all_ips():
                fail_patts.append(r'binding socket to \S* failed')

    if vnc_xml:
        vnc_prepare_cert = params.get("vnc_prepare_cert", "yes")

        expected_vnc_ips = expected_result['vnc_ips']
        vnc_tls = params.get("vnc_tls", "not_set")
        expected_vnc_port = expected_result['vnc_port']

        vnc_fail_patts = []
        if vnc_tls == '1' and vnc_prepare_cert == 'no':
            vnc_fail_patts.append('Failed to find x509 certificates')
            vnc_fail_patts.append('Unable to access credentials')

        if expected_vnc_port != 'not_set':
            if int(expected_vnc_port) < 5900:
                vnc_fail_patts.append('Failed to start VNC server')
                vnc_fail_patts.append(r'vnc port must be in range \[5900,65535\]')
            elif int(expected_vnc_port) > 65535:
                vnc_fail_patts.append('Failed to start VNC server')
                vnc_fail_patts.append(r'vnc port must be in range \[5900,65535\]')
        fail_patts += vnc_fail_patts

        if any([ip not in utils_net.get_all_ips() for ip in expected_vnc_ips]):
            fail_patts.append(r'Cannot assign requested address')

    expected_result['fail_patts'] = fail_patts


def get_expected_spice_options(params, networks, expected_result):
    """
    Predict qemu spice options from parameters.
    """
    x509_dir = params.get("spice_x509_dir", "/etc/pki/libvirt-spice")
    image_compression = params.get("image_compression", "not_set")
    jpeg_compression = params.get("jpeg_compression", "not_set")
    zlib_compression = params.get("zlib_compression", "not_set")
    playback_compression = params.get("playback_compression", "not_set")
    listen_type = params.get("spice_listen_type", "not_set")

    expected_port = expected_result['spice_port']
    expected_tls_port = expected_result['spice_tls_port']

    if listen_type == 'none':
        listen_address = None
    elif listen_type == 'network':
        net_type = params.get("spice_network_type", "vnet")
        listen_address = networks[net_type].ip
    else:
        if listen_type == 'address':
            spice_listen = params.get("spice_listen_address", "127.0.0.1")
        else:
            spice_listen = params.get("spice_listen", "127.0.0.1")

        if spice_listen in ['valid_ipv4', 'valid_ipv6']:
            listen_address = str(expected_result['spice_ips'][0])
        else:
            listen_address = spice_listen

    expected_opts = {
        'addr': listen_address,
        'disable-ticketing': 'yes',
        'seamless-migration': 'on',
    }

    if expected_port != 'not_set':
        expected_opts['port'] = expected_port
    else:
        expected_opts['port'] = ['0', None]

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

    expected_result['spice_options'] = expected_opts


def get_expected_vnc_options(params, networks, expected_result):
    """
    Predict qemu VNC options from parameters.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    x509_dir = params.get("vnc_x509_dir", "/etc/pki/libvirt-vnc")
    vnc_tls = params.get("vnc_tls", "0")
    auto_unix_socket = params.get("vnc_auto_unix_socket", 'not_set')
    tls_x509_verify = params.get("vnc_tls_x509_verify", 'not_set')
    listen_type = params.get("vnc_listen_type", "not_set")

    expected_port = expected_result['vnc_port']

    if listen_type == 'network':
        net_type = params.get("vnc_network_type", "vnet")
        listen_address = networks[net_type].ip
    else:
        if listen_type == 'address':
            vnc_listen = params.get("vnc_listen_address", "127.0.0.1")
        else:
            vnc_listen = params.get("vnc_listen", "127.0.0.1")

        if vnc_listen in ['valid_ipv4', 'valid_ipv6']:
            listen_address = str(expected_result['vnc_ips'][0])
        else:
            listen_address = vnc_listen

    if auto_unix_socket == '1':
        listen_address = 'unix'
        port = [r'/var/lib/libvirt/qemu/\S+%s.vnc' % vm_name,
                r'/var/lib/libvirt/qemu/\S+%s/vnc.sock' % vm_name]
    else:
        if expected_port != 'not_set':
            port = str(int(expected_port) - 5900)
        else:
            port = '0'

    expected_opts = {
        'addr': listen_address,
        'port': port,
    }

    if vnc_tls == '1':
        expected_opts['tls'] = 'yes'
        if tls_x509_verify == '1':
            expected_opts['x509verify'] = x509_dir
        else:
            expected_opts['x509'] = x509_dir

    expected_result['vnc_options'] = expected_opts


def get_expected_results(params, networks):
    """
    Predict all expected results from parameters.
    """
    spice_xml = params.get("spice_xml", "no") == 'yes'
    vnc_xml = params.get("vnc_xml", "no") == 'yes'

    expected_result = {}

    get_expected_listen_ips(params, networks, expected_result)
    try:
        get_expected_ports(params, expected_result)
    except ValueError:
        expected_result['fail_patts'] = ['Unable to find an unused port']
    else:
        get_fail_pattern(params, expected_result)
        if spice_xml:
            get_expected_spice_options(params, networks, expected_result)
        if vnc_xml:
            get_expected_vnc_options(params, networks, expected_result)

    return expected_result


def compare_opts(opts, exp_opts):
    """
    Compare two containers.
    """
    created = set(opts) - set(exp_opts)
    deleted = set(exp_opts) - set(opts)

    # Ignore changed keys that are list containing None.
    created = set(
        key for key in created
        if isinstance(exp_opts[key], list) and None not in exp_opts[key])
    deleted = set(
        key for key in deleted
        if isinstance(exp_opts[key], list) and None not in exp_opts[key])

    if len(created) or len(deleted):
        logging.debug("Created: %s", created)
        logging.debug("Deleted: %s", deleted)
        raise error.TestFail("Expect qemu spice options is %s, but get %s"
                             % (exp_opts, opts))
    else:
        if type(opts) == dict:
            for key in opts:
                if isinstance(exp_opts[key], list):
                    if not all(re.match(opt, opts[key])
                               for opt in exp_opts[key] if opt is not None):
                        logging.debug("Key %s expected one in %s, but got %s",
                                      key, exp_opts[key], opts[key])
                        raise error.TestFail(
                            "Expect qemu options is %s, but get %s"
                            % (exp_opts, opts))
                else:
                    if not re.match(exp_opts[key], opts[key]):
                        logging.debug("Key %s expected %s, but got %s",
                                      key, exp_opts[key], opts[key])
                        raise error.TestFail(
                            "Expect qemu options is %s, but get %s"
                            % (exp_opts, opts))


def check_spice_result(spice_opts,
                       opt_plaintext_channels, opt_tls_channels,
                       expected_result, all_ips):
    """
    Check test results by comparison with expected results.
    """

    expected_spice_ips = expected_result['spice_ips']

    compare_opts(spice_opts, expected_result['spice_options'])
    compare_opts(opt_plaintext_channels, expected_result['plaintext_channels'])
    compare_opts(opt_tls_channels, expected_result['tls_channels'])

    expected_spice_ports = [
        expected_result['spice_port'],
        expected_result['spice_tls_port']
    ]
    expected_spice_ports = [p for p in expected_spice_ports if p != 'not_set']
    logging.info('==\n%s', expected_spice_ports)
    check_addr_port(all_ips, expected_spice_ips, expected_spice_ports)


def check_vnc_result(vnc_opts, expected_result, all_ips):
    """
    Check test results by comparison with expected results.
    """

    expected_vnc_ips = expected_result['vnc_ips']

    compare_opts(vnc_opts, expected_result['vnc_options'])

    expected_vnc_ports = [expected_result['vnc_port']]
    expected_vnc_ports = [p for p in expected_vnc_ports if p != 'not_set']
    check_addr_port(all_ips, expected_vnc_ips, expected_vnc_ports)


def generate_spice_graphic_xml(params, expected_result):
    """
    Generate SPICE graphics XML using input parameters.
    """
    autoport = params.get("spice_autoport", "yes")
    default_mode = params.get("defaultMode", "not_set")
    channels_str = params.get("channels", "")
    port = params.get("spice_port", "not_set")
    tls_port = params.get("spice_tlsPort", "not_set")
    image_compression = params.get("image_compression", "not_set")
    jpeg_compression = params.get("jpeg_compression", "not_set")
    zlib_compression = params.get("zlib_compression", "not_set")
    playback_compression = params.get("playback_compression", "not_set")
    listen_type = params.get("spice_listen_type", "not_set")

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
        net_type = params.get("spice_network_type", "vnet")
        listen = {'type': 'network', 'network': 'virt-test-%s' % net_type}
        graphic.listens = [listen]
    elif listen_type == 'address':
        address = params.get("spice_listen_address", "127.0.0.1")
        if address in ['valid_ipv4', 'valid_ipv6']:
            address = str(expected_result['spice_ips'][0])
        listen = {'type': 'address', 'address': address}
        graphic.listens = [listen]

    return graphic


def generate_vnc_graphic_xml(params, expected_result):
    """
    Generate VNC graphics XML using input parameters.
    """
    autoport = params.get("vnc_autoport", "yes")
    port = params.get("vnc_port", "not_set")
    listen_type = params.get("vnc_listen_type", "not_set")

    graphic = Graphics(type_name='vnc')

    if autoport != 'not_set':
        graphic.autoport = autoport

    if port != 'not_set':
        graphic.port = port

    if listen_type == 'network':
        net_type = params.get("vnc_network_type", "vnet")
        listen = {'type': 'network', 'network': 'virt-test-%s' % net_type}
        graphic.listens = [listen]
    elif listen_type == 'address':
        address = params.get("vnc_listen_address", "127.0.0.1")
        if address in ['valid_ipv4', 'valid_ipv6']:
            address = str(expected_result['vnc_ips'][0])
        listen = {'type': 'address', 'address': address}
        graphic.listens = [listen]
    return graphic


def setup_networks(params):
    """
    Create network according to parameters. return Network instance if
    succeeded or None if no network need to be created.
    """

    networks = {}
    # Find all type of network need to be setup for the tests
    spice_listen_type = params.get("spice_listen_type", "not_set")
    if spice_listen_type == 'network':
        net_type = params.get("spice_network_type", "vnet")
        if net_type not in networks:
            networks[net_type] = None
    vnc_listen_type = params.get("vnc_listen_type", "not_set")
    if vnc_listen_type == 'network':
        net_type = params.get("vnc_network_type", "vnet")
        if net_type not in networks:
            networks[net_type] = None

    # Setup networks
    for net_type in networks:
        if net_type == 'vnet':
            net_name = 'virt-test-%s' % net_type
            address = params.get('vnet_address', '192.168.123.1')
            networks[net_type] = LibvirtNetwork(
                net_type, address=address, net_name=net_name, persistent=True)
        elif net_type == 'macvtap':
            iface = params.get('macvtap_device', 'EXAMPLE.MACVTAP.DEVICE')
            if 'EXAMPLE' in iface:
                raise error.TestNAError('Need to setup macvtap_device first.')
            networks[net_type] = LibvirtNetwork(
                net_type, iface=iface, persistent=True)
        elif net_type == 'bridge':
            iface = params.get('bridge_device', 'EXAMPLE.BRIDGE.DEVICE')
            if 'EXAMPLE' in iface:
                raise error.TestNAError('Need to setup bridge_device first.')
            networks[net_type] = LibvirtNetwork(
                net_type, iface=iface, persistent=True)

    return networks


def block_ports(params):
    """
    Block specified ports.
    """
    block_port = params.get("block_port", None)

    sockets = []
    if block_port is not None:
        block_ports = [int(p) for p in block_port.split(',')]
        for port in block_ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', port))
            sock.listen(5)
            sockets.append(sock)
    return sockets


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
    # Since 3.1.0, '-1' is not an valid value for the VNC port while
    # autoport is disabled, it means the VM will be failed to be
    # started as expected. So, cancel test this case since there is
    # one similar test scenario in the negative_test, which is
    # negative_tests.vnc_only.no_autoport.port_-2
    if libvirt_version.version_compare(3, 1, 0) and (params.get("vnc_port") == '-1'):
        test.cancel('Cancel this case, since it is equivalence class test '
                    'with case negative_tests.vnc_only.no_autoport.port_-2')

    # Since 2.0.0, there are some changes for listen type and port
    # 1. Two new listen types: 'none' and 'socket'(not covered in this test)
    #    In 'none' listen type, no listen address and port is 0,
    #    that means we need reset the listen_type for previous versions,
    #    so we can get the expect result(vm start fail)
    # 2. Spice port accept negative number less than -1
    #    If spice port less than -1, the VM can start normally, but both
    #    listen address and port will be omitted
    spice_listen_type = params.get('spice_listen_type', 'not_set')
    vnc_listen_type = params.get('vnc_listen_type', 'not_set')
    spice_port = params.get('spice_port', 'not_set')
    spice_tlsPort = params.get('spice_tlsPort', 'not_set')
    if not libvirt_version.version_compare(2, 0, 0):
        for listen_type in [spice_listen_type, vnc_listen_type]:
            if listen_type == 'none':
                params[listen_type] = 'not_set'
    else:
        try:
            if int(spice_port) < -1:
                params["negative_test"] = "no"
        except ValueError:
            pass

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    spice_xml = params.get("spice_xml", "no") == 'yes'
    vnc_xml = params.get("vnc_xml", "no") == 'yes'
    is_negative = params.get("negative_test", "no") == 'yes'

    sockets = block_ports(params)
    networks = setup_networks(params)

    expected_result = get_expected_results(params, networks)
    env_state = EnvState(params, expected_result)

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        vm_xml.remove_all_graphics()
        if spice_xml:
            spice_graphic = generate_spice_graphic_xml(params, expected_result)
            logging.debug('Test SPICE XML is: %s', spice_graphic)
            vm_xml.devices = vm_xml.devices.append(spice_graphic)
        if vnc_xml:
            vnc_graphic = generate_vnc_graphic_xml(params, expected_result)
            logging.debug('Test VNC XML is: %s', vnc_graphic)
            vm_xml.devices = vm_xml.devices.append(vnc_graphic)
        vm_xml.sync()
        all_ips = utils_net.get_all_ips()

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

        if spice_xml:
            spice_opts, plaintext_channels, tls_channels = qemu_spice_options(vm)
            check_spice_result(spice_opts, plaintext_channels, tls_channels,
                               expected_result, all_ips)
        if vnc_xml:
            vnc_opts = qemu_vnc_options(vm)
            check_vnc_result(vnc_opts, expected_result, all_ips)

        if is_negative:
            raise error.TestFail("Expect negative result. But start succeed!")
    except error.TestFail, detail:
        bug_url = params.get('bug_url', None)
        if bug_url:
            logging.error("You probably encountered a known bug. "
                          "Please check %s for reference" % bug_url)
        raise
    finally:
        for sock in sockets:
            sock.close()
        if networks:
            for network in networks.values():
                network.cleanup()
        vm_xml_backup.sync()
        os.system('rm -f /dev/shm/spice*')
        env_state.restore()
