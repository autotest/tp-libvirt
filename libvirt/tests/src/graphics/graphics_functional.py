"""
Test libvirt SPICE and VNC features.
"""
import re
import os
import random
import socket
import shutil
import logging
import threading
import time
import datetime
import locale
import base64
import ipaddress
try:
    import queue as Queue
except ImportError:
    import Queue

from avocado.core import exceptions
from avocado.utils import process

from virttest.libvirt_xml.vm_xml import VMXML
from virttest import data_dir
from virttest import virt_vm
from virttest import virsh
from virttest import utils_net
from virttest import utils_misc
from virttest import utils_config
from virttest import utils_libvirtd
from virttest.utils_test import libvirt
from virttest.utils_test.libvirt import LibvirtNetwork
from virttest.libvirt_xml.devices.graphics import Graphics
from virttest.libvirt_xml.secret_xml import SecretXML
from virttest.libvirt_xml import vm_xml

from virttest import libvirt_version

q = Queue.Queue()


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
        vnc_tls_x509_secret_uuid = params.get("vnc_tls_x509_secret_uuid", 'not_set')
        vnc_secret_uuid = params.get("vnc_secret_uuid", "not_set")

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
            expected_ip = str(expected_result['spice_ips'][0].addr)
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

        if vnc_tls_x509_secret_uuid == "not_set":
            del self.qemu_config.vnc_tls_x509_verify
        else:
            self.qemu_config.vnc_tls = "1"
            if vnc_secret_uuid == "invalid":
                self.qemu_config.vnc_tls_x509_secret_uuid = "00000-11111"
            else:
                cmd = "uuidgen"
                status, uuid = process.getstatusoutput(cmd)
                if status:
                    logging.error("Failed to generate valid uuid")
                self.qemu_config.vnc_tls_x509_secret_uuid = uuid

        if vnc_listen == 'not_set':
            del self.qemu_config.vnc_listen
        elif vnc_listen in ['valid_ipv4', 'valid_ipv6']:
            expected_ip = str(expected_result['vnc_ips'][0].addr)
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


def check_addr_port(all_ips, expected_ips, ports, test):
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
                test.fail(
                    'Expect listening on %s:%s but not.' % (ip, port))
            if ip.listening_on(port) and ip not in expected_ips:
                test.fail(
                    'Expect not listening on %s:%s but true.' % (ip, port))


def get_graphic_passwd(libvirt_vm):
    vmxml = VMXML.new_from_dumpxml(libvirt_vm.name, options="--security-info")
    if hasattr(vmxml.get_graphics_devices(), 'passwd'):
        return vmxml.get_graphics_devices()[0].passwd
    else:
        return None


def qemu_spice_options(libvirt_vm):
    """
    Get spice options from VM's qemu command line as dicts.
    """
    pid = libvirt_vm.get_pid()
    res = process.run("ps -p %s -o cmd h" % pid, shell=True)
    match = re.search(r'-spice\s*(\S*)', res.stdout_text)
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

    xml_passwd = get_graphic_passwd(libvirt_vm)
    if xml_passwd is not None:
        spice_dict['passwd'] = xml_passwd
    for key in spice_dict:
        logging.debug("%s: %s", key, spice_dict[key])
    return spice_dict, plaintext_channels, tls_channels


def qemu_vnc_options(libvirt_vm):
    """
    Get VNC options from VM's qemu command line as dicts.
    """
    pid = libvirt_vm.get_pid()
    res = process.run("ps -p %s -o cmd h" % pid, shell=True)
    match = re.search(r'-vnc\s+(\S*)', res.stdout_text)
    if match:
        vnc_opt = match.groups()[0]
    vnc_dict = {}

    vnc_opt_split = vnc_opt.split(',', 1)

    vnc_addr_port = vnc_opt_split[0]
    addr, vnc_dict['port'] = vnc_addr_port.rsplit(':', 1)

    #The format of vnc relevant arguments in qemu command line varies
    #in different RHEL versions, see below:
    #In RHEL6.9, like '-vnc unix:/var/lib/libvirt/qemu/*.vnc'
    #In RHEL7.3, like '-vnc unix:/var/lib/libvirt/qemu/*vnc.sock'
    #In RHEL7.5, like '-vnc vnc=unix:/var/lib/libvirt/qemu/*vnc.sock'
    #In RHEL7.7, like '-vnc vnc=unix:/var/lib/libvirt/qemu/*vnc.sock'

    #Note, for RHEL7.5 and RHEL7.7, 'vnc=' is added before regular arguments.
    #For compatibility, one more handling is added for this change.

    #In RHEL8.0,the format of vnc tls argument in qemu command line is completely different.
    #just as follow:
    #'tls-creds-x509,id=vnc-tls-creds0,dir=/etc/pki/libvirt-vnc,endpoint=server,verify-peer=no
    #-vnc vnc=unix:/var/lib/libvirt/qemu/*/vnc.sock,tls-creds=vnc-tls-creds0'
    addr = addr.split('=')[1] if '=' in addr else addr

    if addr.startswith('[') and addr.endswith(']'):
        addr = addr[1:-1]
    vnc_dict['addr'] = addr

    xml_passwd = get_graphic_passwd(libvirt_vm)
    if len(vnc_opt_split) == 2:
        vnc_opt = vnc_opt_split[1]
        for opt in vnc_opt.split(','):
            if '=' in opt:
                key, value = opt.split('=')
                vnc_dict[key] = value
            else:
                if opt == 'password':
                    if xml_passwd is not None:
                        vnc_dict['passwd'] = xml_passwd
                if opt == 'tls':
                    vnc_dict[opt] = 'yes'

    if libvirt_version.version_compare(5, 0, 0):
        if re.search(r'(tls-creds-x509.*?\s+)', res.stdout_text):
            vnc_out = re.search(r'(tls-creds-x509.*?\s+)', res.stdout_text).group(0)
            if re.match("tls-creds-x509,id=[a-z0-9-]+", vnc_out):
                vnc_dict['tls'] = "yes"
                vnc_dict['id'] = re.match("tls-creds-x509,id=([a-z0-9-]+)", vnc_out).group(1)
            if re.search("dir=[a-z/-]+", vnc_out):
                vnc_dict['dir'] = re.search("dir=([a-z/-]+)", vnc_out).group(1)
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

    def get_global_ipv6():
        """
        Ensure to get the global ipv6 address.
        """
        for i in range(100):
            ip_addr = random_ip('ipv6')
            if str(ip_addr).startswith('fe80:0000'):
                continue
            else:
                return ip_addr

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
            expected_spice_ips = [get_global_ipv6()]
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
            expected_vnc_ips = [get_global_ipv6()]
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
    secure_channels = params.get("secure_channels", "not_set")
    insecure_channels = params.get("insecure_channels", "not_set")
    tls_channels = []
    plaintext_channels = []
    if default_mode == 'secure':
        tls_channels.append('default')
    elif default_mode == 'insecure':
        plaintext_channels.append('default')

    if secure_channels != 'not_set':
        for channel_name in secure_channels.strip().split(':'):
            tls_channels.append(channel_name)
    if insecure_channels != 'not_set':
        for channel_name in insecure_channels.strip().split(':'):
            plaintext_channels.append(channel_name)

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
        elif vnc_listen_type == 'network':
            if vnc_autoport:
                expected_vnc_port = port_allocator.allocate()
            else:
                expected_vnc_port = vnc_port
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
    vnc_secret_uuid = params.get("vnc_secret_uuid", "not_set")

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
                if is_negative and (0 < int(expected_spice_port) <= 65535):
                    fail_patts.append(r'Failed to reserve port')
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

        net_type = params.get('spice_network_type')
        if net_type not in ['nat', 'route']:
            for ip in expected_spice_ips:
                if ip and ip not in utils_net.get_all_ips():
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
        if vnc_secret_uuid == "invalid":
            vnc_fail_patts.append(r'malformed TLS secret uuid')
        elif vnc_secret_uuid == "non_exist":
            vnc_fail_patts.append(r'no secret with matching uuid')
        fail_patts += vnc_fail_patts

        net_type = params.get('vnc_network_type')
        if net_type not in ['nat', 'route']:
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
    graphic_passwd = params.get("graphic_passwd")
    copypaste = params.get("copypaste", "not_set")
    filetransfer = params.get("filetransfer", "not_set")
    streaming_mode = params.get("streaming_mode", "not_set")

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
            listen_address = str(expected_result['spice_ips'][0].addr)
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
    if graphic_passwd:
        expected_opts['passwd'] = graphic_passwd
    if copypaste == 'no':
        expected_opts['disable-copy-paste'] = 'yes'
    elif copypaste == 'yes':
        expected_opts['disable-copy-paste'] = 'no'
    if filetransfer == 'yes':
        expected_opts['disable-agent-file-xfer'] = 'no'
    if streaming_mode != 'not_set':
        expected_opts['streaming-video'] = streaming_mode

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
    graphic_passwd = params.get("graphic_passwd")
    vnc_secret_uuid = params.get("vnc_secret_uuid", "not_set")

    expected_port = expected_result['vnc_port']

    expected_opts = {}

    if listen_type == 'network':
        net_type = params.get("vnc_network_type", "vnet")
        listen_address = networks[net_type].ip
    else:
        if listen_type == 'address':
            vnc_listen = params.get("vnc_listen_address", "127.0.0.1")
        else:
            vnc_listen = params.get("vnc_listen", "127.0.0.1")

        if vnc_listen in ['valid_ipv4', 'valid_ipv6']:
            listen_address = str(expected_result['vnc_ips'][0].addr)
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

    if libvirt_version.version_compare(7, 2, 0):
        # libvirt auto-populate <audio> elements for vnc since 7.2.0
        vmxml = VMXML.new_from_dumpxml(vm_name)
        audio_id = vmxml.get_devices(device_type="audio")[0].id
        expected_opts['audiodev'] = "audio" + audio_id

    if vnc_tls == '1':
        expected_opts['tls'] = 'yes'
        if tls_x509_verify == '1':
            expected_opts['x509verify'] = x509_dir
        if libvirt_version.version_compare(5, 0, 0):
            expected_opts['dir'] = x509_dir
            expected_opts['id'] = "vnc-tls-creds0"
            expected_opts['tls-creds'] = "vnc-tls-creds0"
        else:
            expected_opts['x509'] = x509_dir

    if graphic_passwd:
        expected_opts['passwd'] = graphic_passwd
    if vnc_secret_uuid == "valid":
        expected_opts['vnc-secret'] = "vnc-tls-creds0-secret0"
        expected_opts['tls'] = 'yes'
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


def compare_opts(opts, exp_opts, test):
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
        test.fail("Expect qemu spice options is %s, but get %s"
                  % (exp_opts, opts))
    else:
        if type(opts) == dict:
            for key in opts:
                if isinstance(exp_opts[key], list):
                    if not all(re.match(opt, opts[key])
                               for opt in exp_opts[key] if opt is not None):
                        logging.debug("Key %s expected one in %s, but got %s",
                                      key, exp_opts[key], opts[key])
                        test.fail(
                            "Expect qemu options is %s, but get %s"
                            % (exp_opts, opts))
                else:
                    if not re.match(exp_opts[key], opts[key]):
                        logging.debug("Key %s expected %s, but got %s",
                                      key, exp_opts[key], opts[key])
                        test.fail(
                            "Expect qemu options is %s, but get %s"
                            % (exp_opts, opts))


def check_spice_result(spice_opts,
                       opt_plaintext_channels, opt_tls_channels,
                       expected_result, all_ips, test):
    """
    Check test results by comparison with expected results.
    """

    expected_spice_ips = expected_result['spice_ips']

    compare_opts(spice_opts, expected_result['spice_options'], test)
    compare_opts(opt_plaintext_channels, expected_result['plaintext_channels'], test)
    compare_opts(opt_tls_channels, expected_result['tls_channels'], test)

    expected_spice_ports = [
        expected_result['spice_port'],
        expected_result['spice_tls_port']
    ]
    expected_spice_ports = [p for p in expected_spice_ports if p != 'not_set']
    logging.info('==\n%s', expected_spice_ports)
    check_addr_port(all_ips, expected_spice_ips, expected_spice_ports, test)


def check_vnc_result(vnc_opts, expected_result, all_ips, test):
    """
    Check test results by comparison with expected results.
    """

    expected_vnc_ips = expected_result['vnc_ips']

    compare_opts(vnc_opts, expected_result['vnc_options'], test)

    expected_vnc_ports = [expected_result['vnc_port']]
    expected_vnc_ports = [p for p in expected_vnc_ports if p != 'not_set']
    check_addr_port(all_ips, expected_vnc_ips, expected_vnc_ports, test)


def set_passwd_valid_time_in_graphic(graphic, valid_time, graphic_passwd):
    """
    Set password valid time

    :param graphic: a graphics device XML
    :param valid_time: password valid time
    :param graphic_passwd: graphic password
    :return graphic: a graphics device XML
    """
    if valid_time:
        assert graphic_passwd != ""
        assert graphic_passwd is not None
        graphic.passwd = graphic_passwd
        start = datetime.datetime.utcnow()
        end = start + datetime.timedelta(seconds=int(valid_time))
        graphic.passwdValidTo = end.strftime("%Y-%m-%dT%H:%M:%S")
    return graphic


def generate_spice_graphic_xml(params, expected_result):
    """
    Generate SPICE graphics XML using input parameters.
    """
    autoport = params.get("spice_autoport", "yes")
    default_mode = params.get("defaultMode", "not_set")
    secure_channels = params.get("secure_channels", "not_set")
    insecure_channels = params.get("insecure_channels", "not_set")
    port = params.get("spice_port", "not_set")
    tls_port = params.get("spice_tlsPort", "not_set")
    image_compression = params.get("image_compression", "not_set")
    jpeg_compression = params.get("jpeg_compression", "not_set")
    zlib_compression = params.get("zlib_compression", "not_set")
    playback_compression = params.get("playback_compression", "not_set")
    listen_type = params.get("spice_listen_type", "not_set")
    graphic_passwd = params.get("graphic_passwd")
    spice_passwd_place = params.get("spice_passwd_place", "not_set")
    valid_time = params.get("valid_time")
    copypaste = params.get("copypaste", "not_set")
    filetransfer = params.get("filetransfer", "not_set")
    streaming_mode = params.get("streaming_mode", "not_set")

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

    if copypaste != 'not_set':
        graphic.clipboard_copypaste = copypaste

    if filetransfer != 'not_set':
        graphic.filetransfer_enable = filetransfer

    if streaming_mode != 'not_set':
        graphic.streaming_mode = streaming_mode

    channels = []
    if secure_channels != 'not_set':
        for secure_name in secure_channels.strip().split(':'):
            channels.append({'name': secure_name, 'mode': 'secure'})
    if insecure_channels != 'not_set':
        for insecure_name in insecure_channels.strip().split(':'):
            channels.append({'name': insecure_name, 'mode': 'insecure'})
    graphic.channel = channels

    if listen_type == 'network':
        net_type = params.get("spice_network_type", "vnet")
        if net_type == 'macvtap':
            address = str(expected_result['spice_ips'][0].addr)
        else:
            address = params.get("listen_address", "not_set")
        listen = {'type': 'network', 'network': 'virt-test-%s' % net_type, 'address': address}
        graphic.listens = [listen]
    elif listen_type == 'address':
        address = params.get("spice_listen_address", "127.0.0.1")
        if address in ['valid_ipv4', 'valid_ipv6']:
            address = str(expected_result['spice_ips'][0].addr)
        listen = {'type': 'address', 'address': address}
        graphic.listens = [listen]

    if graphic_passwd and spice_passwd_place == "guest":
        graphic.passwd = graphic_passwd

    # set password valid time
    graphic = set_passwd_valid_time_in_graphic(graphic, valid_time, graphic_passwd)
    return graphic


def generate_vnc_graphic_xml(params, expected_result):
    """
    Generate VNC graphics XML using input parameters.
    """
    autoport = params.get("vnc_autoport", "yes")
    port = params.get("vnc_port", "not_set")
    listen_type = params.get("vnc_listen_type", "not_set")
    graphic_passwd = params.get("graphic_passwd")
    vnc_passwd_place = params.get("vnc_passwd_place", "not_set")
    valid_time = params.get("valid_time")

    graphic = Graphics(type_name='vnc')

    if autoport != 'not_set':
        graphic.autoport = autoport

    if port != 'not_set':
        graphic.port = port

    if listen_type == 'network':
        net_type = params.get("vnc_network_type", "vnet")
        if net_type == 'macvtap':
            address = str(expected_result['vnc_ips'][0].addr)
        else:
            address = params.get("listen_address", "not_set")
        listen = {'type': 'network', 'network': 'virt-test-%s' % net_type, 'address': address}
        graphic.listens = [listen]
    elif listen_type == 'address':
        address = params.get("vnc_listen_address", "127.0.0.1")
        if address in ['valid_ipv4', 'valid_ipv6']:
            address = str(expected_result['vnc_ips'][0].addr)
        listen = {'type': 'address', 'address': address}
        graphic.listens = [listen]

    if graphic_passwd and vnc_passwd_place == "guest":
        graphic.passwd = graphic_passwd

    # set password valid time
    graphic = set_passwd_valid_time_in_graphic(graphic, valid_time, graphic_passwd)
    return graphic


def setup_networks(params, test):
    """
    Create network according to parameters. return Network instance if
    succeeded or None if no network need to be created.
    """

    networks = {}
    # Find all type of network need to be setup for the tests
    spice_listen_type = params.get("spice_listen_type", "not_set")
    if spice_listen_type == 'network':
        net_type = params.get("spice_network_type", "vnet")
        spice_listen = params.get("spice_listen")
        if net_type not in networks:
            networks[net_type] = None
    vnc_listen_type = params.get("vnc_listen_type", "not_set")
    if vnc_listen_type == 'network':
        net_type = params.get("vnc_network_type", "vnet")
        vnc_listen = params.get("vnc_listen")
        if net_type not in networks:
            networks[net_type] = None

    ip_version = params.get('ip_version', 'not_set')
    if ip_version == 'ipv6':
        # Enabling IPv6 forwarding with RA routes without accept_ra set to 2
        # is likely to cause routes loss
        sysctl_cmd = 'sysctl net.ipv6.conf.all.accept_ra'
        original_accept_ra = process.run(sysctl_cmd + ' -n', shell=True).stdout_text
        if original_accept_ra != '2':
            process.system(sysctl_cmd + '=2')

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
                test.cancel('Need to setup macvtap_device first.')
            networks[net_type] = LibvirtNetwork(
                net_type, iface=iface, persistent=True)
        elif net_type == 'bridge':
            iface = params.get('bridge_device', 'EXAMPLE.BRIDGE.DEVICE')
            if 'EXAMPLE' in iface:
                test.cancel('Need to setup bridge_device first.')
            networks[net_type] = LibvirtNetwork(
                net_type, iface=iface, persistent=True)
        elif net_type in ['nat', 'route']:
            net_kwargs = {'net_name': 'virt-test-%s' % net_type,
                          'br_name': 'br_%s' % net_type,
                          'address': params.get('listen_address', 'not_set'),
                          'dhcp_start': params.get('network_dhcp_start', 'not_set'),
                          'dhcp_end': params.get('network_dhcp_end', 'not_set'),
                          'ip_version': ip_version,
                          'persistent': True}
            networks[net_type] = LibvirtNetwork(net_type, **net_kwargs)

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


def run_remote_viewer(virt_viewer_file, opt_str):
    """
    Run remote-viewer command

    :param virt_viewer_file: remote-viewer's config file
    :param opt_str: remote-viewer parameter
    """
    if not opt_str:
        opt_str = ""
    cmd = "remote-viewer --debug %s %s" % (opt_str, virt_viewer_file)
    res = process.run(cmd, shell=True, verbose=True, ignore_status=True)
    q.put(res.stdout_text)


def check_connection(graphic_type, port, graphic_passwd, ip, test, rv_log_str, rv_log_auth, opt_str, valid_time):
    """
    Use remote-viewer to connect guest

    :param graphic_type: graphic type
    :param port: port value
    :param ip: graphic listen address value
    :param graphic_passwd: graphic password
    :param test: test instance
    :param rv_log_str: remote-viewer debug log string
    :param rv_log_auth: authentication failed information in remote-viewer debug log
    :param opt_str: remote-viewer parameter
    :param valid_time: password valid time
    """

    if graphic_passwd:
        virt_viewer_conf = """
[virt-viewer]
type=%s
host=%s
port=%s
username=root
password=%s
""" % (graphic_type, ip, port, graphic_passwd)
    else:
        virt_viewer_conf = """
[virt-viewer]
type=%s
host=%s
port=%s
username=root
""" % (graphic_type, ip, port)

    logging.debug("remote-viewer config: %s" % virt_viewer_conf)
    # Write virt-viewer config into a tmpfile
    virt_viewer_file = os.path.join(data_dir.get_tmp_dir(), "avocado_virt_viewer")
    with open(virt_viewer_file, 'w') as fd:
        fd.write(virt_viewer_conf)
        time.sleep(1)

    try:
        if valid_time:
            # run remote-viewer command
            rv_start = threading.Thread(target=run_remote_viewer,
                                        args=(virt_viewer_file, opt_str))
            rv_start.start()
            rv_start.join(int(valid_time) + 3)

            # check connection exist or not
            res = process.run("pidof remote-viewer", shell=True, verbose=True)
            if res.exit_status:
                test.fail("Connection fail after password expired.")
            else:
                logging.info("Keep connection after password expired.")

            # stop remote-viewer command
            utils_misc.kill_process_by_pattern("remote-viewer")

            rv_result = q.get()
            # check review-viewer log
            if (rv_log_str in rv_result) and (rv_log_auth not in rv_result):
                logging.info("Password expired but remote-viewer keep connection.")
            else:
                test.fail('After password expired, connection closed.')

        # check guest connection
        rv_start = threading.Thread(target=run_remote_viewer,
                                    args=(virt_viewer_file, opt_str))
        rv_start.start()
        rv_start.join(3)

        # stop remote-viewer command
        utils_misc.kill_process_by_pattern("remote-viewer")

        rv_result = q.get()
        if valid_time:
            cmp_str = rv_log_auth
        else:
            cmp_str = rv_log_str
        if cmp_str in rv_result:
            logging.info("Using remote-viewer to check guest successful.")
        else:
            test.fail('Using remote-viewer to check guest failed.')
    finally:
        if os.path.exists(virt_viewer_file):
            os.remove(virt_viewer_file)


def create_secret(secret_uuid, secret_password):
    """
       Create secret:

       :param secret_uuid: uuid of secret
       :param secret_password: password for secret
    """
    sec_xml = SecretXML("no", "yes")
    sec_xml.uuid = secret_uuid
    sec_xml.description = "tls secret"
    sec_xml.usage = 'tls'
    sec_xml.usage_name = "TLS_exampl"
    sec_xml.xmltreefile.write()

    ret = virsh.secret_define(sec_xml.xml)
    libvirt.check_exit_status(ret)
    # Get secret uuid
    try:
        encryption_uuid = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                     ret.stdout.strip())[0].lstrip()
    except IndexError as e:
        logging.error("Fail to get newly created secret uuid")
    logging.debug("Secret uuid %s", encryption_uuid)

    # Set secret value.
    encoding = locale.getpreferredencoding()
    secret_string = base64.b64encode(secret_password.encode(encoding)).decode(encoding)
    ret = virsh.secret_set_value(encryption_uuid, secret_string, debug=True)
    libvirt.check_exit_status(ret)
    return encryption_uuid


def check_xml(vm_name, filetransfer, test):
    """
    Check guest xml

    :param vm_name: name of the VM domain
    :param filetransfer: the attribute of filetransfer element
    :param test: test object
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    graphic = vmxml.xmltreefile.find('devices').find('graphics')
    if graphic.find('filetransfer').get('enable') != filetransfer:
        test.fail('The attribute of filetransfer error.')


def check_domdisplay_result(graphic_type, vm_name, expected_result, test):
    """
    Check domdisplay result

    :param graphic_type: graphic type
    :param vm_name: name of the VM domain
    :param expected_result: expected result
    :param test: test object
    """
    domdisplay_out = virsh.domdisplay(vm_name, debug=True)
    if domdisplay_out.exit_status:
        test.fail("Fail to get domain display info. Error:"
                  "%s." % domdisplay_out.stderr.strip())
    expected_uri = "%s://" % graphic_type
    if graphic_type == 'spice':
        expected_uri += "%s:" % expected_result['spice_options']['addr']
        expected_uri += "%s" % expected_result['spice_port']
        if expected_result['spice_tls_port'] != 'not_set':
            expected_uri += "?tls-port=%s" % expected_result['spice_tls_port']
    if graphic_type == 'vnc':
        expected_uri += "%s:" % expected_result['vnc_options']['addr']
        expected_uri += "%s" % expected_result['vnc_port']
    if domdisplay_out.stdout.strip() != expected_uri:
        test.fail("Use domdisplay to check URI failed. Expected uri: %s" % expected_uri)


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
    graphic_passwd = params.get("graphic_passwd")
    remote_viewer_check = params.get("remote_viewer_check", "no") == 'yes'
    valid_time = params.get("valid_time")
    vnc_secret_uuid = params.get("vnc_secret_uuid", "not_set")
    secret_password = params.get("secret_password", "redhat")
    filetransfer = params.get("filetransfer", "not_set")
    default_mode = params.get("defaultMode", "not_set")
    insecure_channels = params.get("insecure_channels", "not_set")
    autoport = params.get("spice_autoport", "yes")
    spice_tls = params.get("spice_tls", "not_set")

    sockets = block_ports(params)
    networks = setup_networks(params, test)

    expected_result = get_expected_results(params, networks)
    env_state = EnvState(params, expected_result)

    vm = env.get_vm(vm_name)
    virsh_instance = None
    if is_negative and spice_xml and autoport == 'no' and spice_tls == '0':
        virsh_instance = virsh.VirshPersistent(uri="qemu:///system")
        vm_xml = VMXML.new_from_inactive_dumpxml(vm_name, virsh_instance=virsh_instance)
    else:
        vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()

    secret_uuid = ""
    if vnc_secret_uuid == "valid":
        secret_uuid = create_secret(config.vnc_tls_x509_secret_uuid, secret_password)

    if graphic_passwd:
        spice_passwd_place = params.get("spice_passwd_place", "not_set")
        vnc_passwd_place = params.get("vnc_passwd_place", "not_set")
        if spice_passwd_place == "qemu":
            config['spice_password'] = '"%s"' % graphic_passwd
            libvirtd.restart()
        if vnc_passwd_place == "qemu":
            config['vnc_password'] = '"%s"' % graphic_passwd
            libvirtd.restart()
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
        fail_patts = expected_result['fail_patts']
        if is_negative and spice_xml and autoport == 'no' and spice_tls == '0':
            virsh_instance.remove_domain(vm_name)
            result = virsh_instance.define(vm_xml.xml, ignore_status=True)
            if result.exit_status:
                logging.debug("Define %s failed as expected.\n"
                              "Detail: %s.", vm_name, result)
                for patt in fail_patts:
                    if re.search(patt, result.stdout):
                        return
                test.fail(
                    "Expect fail with error in %s, but failed with: %s"
                    % (fail_patts, result.stdout))
        else:
            vm_xml.sync()
        all_ips = utils_net.get_all_ips()
        if spice_listen_type == "network" or vnc_listen_type == "network":
            for ip in all_ips:
                ip.addr = ipaddress.ip_address(ip.addr).compressed
        try:
            vm.start()
        except virt_vm.VMStartError as detail:
            if not fail_patts:
                test.fail(
                    "Expect VM can be started, but failed with: %s" % detail)
            for patt in fail_patts:
                if re.search(patt, str(detail)):
                    return
            test.fail(
                "Expect fail with error in %s, but failed with: %s"
                % (fail_patts, detail))
        else:
            if fail_patts:
                test.fail(
                    "Expect VM can't be started with %s, but started."
                    % fail_patts)

        if spice_xml:
            spice_opts, plaintext_channels, tls_channels = qemu_spice_options(vm)
            check_spice_result(spice_opts, plaintext_channels, tls_channels,
                               expected_result, all_ips, test)
            # Check the attribute of filetransfer in guest xml
            if filetransfer != 'not_set':
                check_xml(vm_name, filetransfer, test)
            # When defaultMode=secure and all channels=insecure, use domdisplay to check uri
            if default_mode == 'secure' and insecure_channels != 'not_set':
                channel_name = insecure_channels.strip().split(':')
                if len(channel_name) == 8:
                    check_domdisplay_result('spice', vm_name, expected_result, test)
            # Use remote-viewer to connect guest
            if remote_viewer_check:
                rv_log_str = params.get("rv_log_str")
                rv_log_auth = params.get("rv_log_auth")
                opt_str = params.get('opt_str')
                check_connection('spice', expected_result['spice_port'], graphic_passwd,
                                 expected_result['spice_options']['addr'], test, rv_log_str,
                                 rv_log_auth, opt_str, valid_time)

        if vnc_xml:
            vnc_opts = qemu_vnc_options(vm)
            check_vnc_result(vnc_opts, expected_result, all_ips, test)
            # Use remote-viewer to connect guest
            if remote_viewer_check:
                rv_log_str = params.get("rv_log_str")
                rv_log_auth = params.get("rv_log_auth")
                opt_str = params.get('opt_str')
                check_connection('vnc', expected_result['vnc_port'], graphic_passwd,
                                 expected_result['vnc_options']['addr'], test, rv_log_str,
                                 rv_log_auth, opt_str, valid_time)

        if is_negative:
            test.fail("Expect negative result. But start succeed!")
    except exceptions.TestFail as detail:
        bug_url = params.get('bug_url', None)
        if bug_url:
            logging.error("You probably encountered a known bug. "
                          "Please check %s for reference" % bug_url)
        raise
    finally:
        for sock in sockets:
            sock.close()
        if networks:
            for network in list(networks.values()):
                network.cleanup()
        if vnc_secret_uuid == 'valid' and secret_uuid != "":
            virsh.secret_undefine(config.vnc_tls_x509_secret_uuid, debug=True)
        vm_xml_backup.sync()
        os.system('rm -f /dev/shm/spice*')
        env_state.restore()
        libvirtd.restart()
