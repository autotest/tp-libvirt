import os
import logging
import time
import errno
import socket
import threading
import select
import platform
import subprocess
import re

import aexpect

from virttest import remote
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.utils_conn import TLSConnection
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices import librarian
from virttest.libvirt_xml.devices.graphics import Graphics

from virttest import libvirt_version

from avocado.utils import astring


class Console(aexpect.ShellSession):

    """
    This class create a socket or file/pipe descriptor for console connect
    with qemu VM in different ways.
    """

    def __init__(self, console_type, address, is_server=False, pki_path='.'):
        """
        Initialize the instance and create socket/fd for connect with.

        :param console_type: Type of console to connect with. Supports
                             unix, tcp, udp, file or pipe
        :param address: Address for socket or fd. For tcp and udp, This should
                        be a tuple of (host, port). For unix, file, pipe
                        this should be a string representing the path to be
                        connect with
        :param is_server: Whether this connection act as a server or a client
        :param pki_path: Where the tls pki file is located
        """
        self.exit = False
        self.address = address
        self.peer_addr = None
        self.console_type = console_type
        self.socket = None
        self.read_fd = None
        self.write_fd = None
        self._poll_thread = None
        self.pki_path = pki_path
        self.linesep = "\n"
        self.prompt = r"^\[.*\][\#\$]\s*$"
        self.status_test_command = "echo $?"
        self.process = None

        if console_type == 'unix':
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket.connect(address)
        elif console_type == 'tls':
            outfilename = '/tmp/gnutls.out'
            self.read_fd = open(outfilename, 'a+')
            if is_server:
                cmd = ('gnutls-serv --echo --x509cafile %s/ca-cert.pem ' %
                       self.pki_path + '--x509keyfile %s/server-key.pem' %
                       self.pki_path + ' --x509certfile %s/server-cert.pem' %
                       self.pki_path)
                obj = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
                                       stdout=self.read_fd,
                                       universal_newlines=True)
            else:
                cmd = ('gnutls-cli --priority=NORMAL -p5556 --x509cafile=%s'
                       % self.pki_path + '/ca-cert.pem ' + '127.0.0.1 '
                       + '--x509certfile=%s' % self.pki_path
                       + '/client-cert.pem --x509keyfile=%s' % self.pki_path
                       + '/client-key.pem')
                obj = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
                                       stdout=self.read_fd,
                                       universal_newlines=True)
                obj.stdin.write('\n')
                time.sleep(50)
                obj.stdin.write('\n')
                time.sleep(50)
                # In python 3, stdin.close() will raise a BrokenPiPeError
                try:
                    obj.stdin.close()
                except socket.error as e:
                    if e.errno != errno.EPIPE:
                        # Not a broken pipe
                        raise
            self.process = obj
        elif console_type == 'tcp':
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if is_server:
                self.socket.bind(address)
                self.socket.listen(1)
                self._poll_thread = threading.Thread(
                    target=self._tcp_thread)
                self._poll_thread.start()
            else:
                self.socket.connect(address)
        elif console_type == 'udp':
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if is_server:
                self.socket.bind(address)
        elif console_type == 'pipe':
            os.mkfifo(address + '.in')
            os.mkfifo(address + '.out')
            self.write_fd = os.open(
                address + '.in',
                os.O_RDWR | os.O_CREAT | os.O_NONBLOCK)
            self.read_fd = os.open(
                address + '.out',
                os.O_RDONLY | os.O_CREAT | os.O_NONBLOCK)
        elif console_type == 'file':
            self.read_fd = open(address,
                                'r')

    def _tcp_thread(self):
        """
        Thread function to wait for TCP connection.
        """
        socket, addr = self.socket.accept()
        logging.debug("TCP connection established from %s", addr)
        self.socket = socket

    def read_nonblocking(self, internal_timeout=None, timeout=None):
        """
        Read from socket/fd until there is nothing to read for timeout seconds.

        :param internal_timeout: Time (seconds) to wait before we give up
                                 reading from the child process, or None to
                                 use the default value.
        :param timeout: Timeout for reading child process output.
        """
        if internal_timeout is None:
            internal_timeout = 0.1
        end_time = None
        if timeout:
            end_time = time.time() + timeout
        data = ""
        while True:
            try:
                if self.console_type in ['tcp', 'unix']:
                    new_data = self.socket.recv(
                        1024, socket.MSG_DONTWAIT)
                    data += astring.to_text(new_data, errors='ignore')
                    return data
                elif self.console_type == 'udp':
                    new_data, self.peer_addr = self.socket.recvfrom(
                        1024, socket.MSG_DONTWAIT)
                    data += astring.to_text(new_data, errors='ignore')
                    return data
                elif self.console_type in ['file', 'pipe', 'tls']:
                    try:
                        read_fds, _, _ = select.select(
                            [self.read_fd], [], [], internal_timeout)
                    except Exception:
                        return data
                    if self.read_fd in read_fds:
                        if self.console_type == 'pipe':
                            new_data = os.read(self.read_fd, 1024)
                        else:
                            new_data = self.read_fd.read(1024)
                        if not new_data:
                            return data
                        data += new_data
                    else:
                        return data
            except socket.error as detail:
                err = detail.args[0]
                if err in [errno.EAGAIN, errno.EWOULDBLOCK]:
                    return data
                else:
                    raise detail
            except OSError as detail:
                err = detail.args[0]
                if err in [errno.EAGAIN, errno.EWOULDBLOCK]:
                    return data
                else:
                    raise detail
            if end_time and time.time() > end_time:
                return data

    def read_until_output_matches(self, patterns, filter_func=lambda x: x,
                                  timeout=60, internal_timeout=None,
                                  print_func=None, match_func=None):
        """
        Read from socket/fd using read_nonblocking until a pattern matches.

        :param patterns: List of strings (regular expression patterns)
        :param filter_func: Function to apply to the data read from the child
                before attempting to match it against the patterns (should take
                and return a string)
        :param timeout: The duration (in seconds) to wait until a match is
                found
        :param internal_timeout: The timeout to pass to read_nonblocking
        :param print_func: A function to be used to print the data being read
                (should take a string parameter)
        :param match_func: Function to compare the output and patterns.
        :return: Tuple containing the match index and the data read so far
        :raise ExpectTimeoutError: Raised if timeout expires
        :raise ExpectProcessTerminatedError: Raised if the child process
                terminates while waiting for output
        :raise ExpectError: Raised if an unknown error occurs
        """
        if not match_func:
            match_func = self.match_patterns
        output = ""
        end_time = time.time() + timeout
        while True:
            data = self.read_nonblocking(internal_timeout,
                                         end_time - time.time())
            # Print it if necessary
            if print_func:
                for line in data.splitlines():
                    print_func(line)
            # Look for patterns
            output += data
            match = match_func(filter_func(output), patterns)
            if match is not None:
                return match, output

    def wait_for_login(self, username, password,
                       timeout=240, internal_timeout=10):
        """
        Make multiple attempts to log into the guest via serial console.

        :param timeout: Time (seconds) to keep trying to log in.
        :param internal_timeout: Timeout to pass to handle_prompts().
        """
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                self.sendline()
                remote.handle_prompts(self, username, password,
                                      self.prompt, internal_timeout)
                return
            except aexpect.ExpectTimeoutError as detail:
                raise remote.LoginTimeoutError(detail.output)
            except aexpect.ExpectProcessTerminatedError as detail:
                raise remote.LoginProcessTerminatedError(
                    detail.status, detail.output)
            time.sleep(2)
        # Timeout expired; try one more time but don't catch exceptions
        self.sendline()
        remote.handle_prompts(self, username, password, self.prompt, timeout)

    def send(self, cont=""):
        """
        Send a string to the socket/fd.

        :param cont: String to send to the child process.
        """
        if self.console_type in ['tcp', 'unix']:
            self.socket.sendall(cont.encode())
        elif self.console_type == 'udp':
            if self.peer_addr is None:
                logging.debug("No peer connection yet.")
            else:
                self.socket.sendto(cont.encode(), self.peer_addr)
        elif self.console_type == 'pipe':
            try:
                os.write(self.write_fd, cont.encode())
            except Exception:
                pass
        elif self.console_type == 'file':
            logging.debug("File console don't support send!")

    def __del__(self):
        self.exit = True
        if self._poll_thread:
            self._poll_thread.join()
        if self.socket is not None:
            self.socket.close()
        if self.read_fd is not None:
            if self.console_type == 'pipe':
                os.close(self.read_fd)
            else:
                self.read_fd.close()
        if self.write_fd is not None:
            if self.console_type == 'pipe':
                os.close(self.write_fd)
            else:
                self.write_fd.close()
        if self.console_type == 'pipe':
            if os.path.exists(self.address + '.in'):
                os.remove(self.address + '.in')
            if os.path.exists(self.address + '.out'):
                os.remove(self.address + '.out')
        if self.process is not None:
            self.process.terminate()


def run(test, params, env):
    """
    Test for basic serial character device function.

    1) Define the VM with specified serial device and define result meets
       expectation.
    2) Test whether defined XML meets expectation
    3) Start the guest and check if start result meets expectation
    4) Test the function of started serial device
    5) Shutdown the VM and check whether cleaned up properly
    6) Clean up environment
    """

    def set_targets(serial):
        """
        Prepare a serial device XML according to parameters
        """
        machine = platform.machine()
        if "ppc" in machine:
            serial.target_model = 'spapr-vty'
            serial.target_type = 'spapr-vio-serial'
        elif "aarch" in machine:
            serial.target_model = 'pl011'
            serial.target_type = 'system-serial'
        elif "s390x" in machine and 'sclp' not in serial_type:
            if target_model:
                serial.target_model = target_model
            else:
                serial.target_model = 'sclpconsole'
            serial.target_type = 'sclp-serial'
        else:
            serial.target_model = target_type
            serial.target_type = target_type

    def prepare_spice_graphics_device():
        """
        Prepare a spice graphics device XML according to parameters
        """
        graphic = Graphics(type_name='spice')
        graphic.autoport = "yes"
        graphic.port = "-1"
        graphic.tlsPort = "-1"
        return graphic

    def prepare_serial_device():
        """
        Prepare a serial device XML according to parameters
        """
        local_serial_type = serial_type
        if serial_type == "tls":
            local_serial_type = "tcp"
        serial = librarian.get('serial')(local_serial_type)

        serial.target_port = "0"

        set_targets(serial)

        sources = []
        logging.debug(sources_str)
        for source_str in sources_str.split():
            source_dict = {}
            for att in source_str.split(','):
                key, val = att.split(':')
                source_dict[key] = val
            sources.append(source_dict)
        serial.sources = sources
        return serial

    def prepare_console_device():
        """
        Prepare a serial device XML according to parameters
        """
        local_serial_type = serial_type
        if serial_type == "tls":
            local_serial_type = "tcp"
        console = librarian.get('console')(local_serial_type)
        console.target_type = console_target_type
        console.target_port = console_target_port

        sources = []
        logging.debug(sources_str)
        for source_str in sources_str.split():
            source_dict = {}
            for att in source_str.split(','):
                key, val = att.split(':')
                source_dict[key] = val
            sources.append(source_dict)
        console.sources = sources
        return console

    def define_and_check():
        """
        Predict the error message when defining and try to define the guest
        with testing serial device.
        """
        fail_patts = []
        if serial_type in ['dev', 'file', 'pipe', 'unix'] and not any(
                ['path' in s for s in serial_dev.sources]):
            fail_patts.append(r"Missing source path attribute for char device")
        if serial_type in ['tcp'] and not any(
                ['host' in s for s in serial_dev.sources]):
            fail_patts.append(r"Missing source host attribute for char device")
        if serial_type in ['tcp', 'udp'] and not any(
                ['service' in s for s in serial_dev.sources]):
            fail_patts.append(r"Missing source service attribute for char "
                              "device")
        if serial_type in ['spiceport'] and not any(
                ['channel' in s for s in serial_dev.sources]):
            fail_patts.append(r"Missing source channel attribute for char "
                              "device")
        if serial_type in ['nmdm'] and not any(
                ['master' in s for s in serial_dev.sources]):
            fail_patts.append(r"Missing master path attribute for nmdm device")
        if serial_type in ['nmdm'] and not any(
                ['slave' in s for s in serial_dev.sources]):
            fail_patts.append(r"Missing slave path attribute for nmdm device")
        if serial_type in ['spicevmc']:
            fail_patts.append(r"spicevmc device type only supports virtio")

        vm_xml.undefine()
        res = vm_xml.virsh.define(vm_xml.xml)
        libvirt.check_result(res, expected_fails=fail_patts)
        return not res.exit_status

    def check_xml():
        """
        Predict the result serial device and generated console device
        and check the result domain XML against expectation
        """
        console_cls = librarian.get('console')

        local_serial_type = serial_type

        if serial_type == 'tls':
            local_serial_type = 'tcp'
        # Predict expected serial and console XML
        expected_console = console_cls(local_serial_type)

        if local_serial_type == 'udp':
            sources = []
            for source in serial_dev.sources:
                if 'service' in source and 'mode' not in source:
                    source['mode'] = 'connect'
                sources.append(source)
        else:
            sources = serial_dev.sources

        expected_console.sources = sources

        if local_serial_type == 'tcp':
            if 'protocol_type' in local_serial_type:
                expected_console.protocol_type = serial_dev.protocol_type
            else:
                expected_console.protocol_type = "raw"

        expected_console.target_port = serial_dev.target_port
        if 'target_type' in serial_dev:
            expected_console.target_type = serial_dev.target_type
        expected_console.target_type = console_target_type
        logging.debug("Expected console XML is:\n%s", expected_console)

        # Get current serial and console XML
        current_xml = VMXML.new_from_dumpxml(vm_name)
        serial_elem = current_xml.xmltreefile.find('devices/serial')
        console_elem = current_xml.xmltreefile.find('devices/console')
        if console_elem is None:
            test.fail("Expect generate console automatically, "
                      "but found none.")
        if serial_elem and console_target_type != 'serial':
            test.fail("Don't Expect exist serial device, "
                      "but found:\n%s" % serial_elem)

        cur_console = console_cls.new_from_element(console_elem)
        logging.debug("Current console XML is:\n%s", cur_console)
        # Compare current serial and console with oracle.
        if not expected_console == cur_console:
            # "==" has been override
            test.fail("Expect generate console:\n%s\nBut got:\n%s" %
                      (expected_console, cur_console))

        if console_target_type == 'serial':
            serial_cls = librarian.get('serial')
            expected_serial = serial_cls(local_serial_type)
            expected_serial.sources = sources

            set_targets(expected_serial)

            if local_serial_type == 'tcp':
                if 'protocol_type' in local_serial_type:
                    expected_serial.protocol_type = serial_dev.protocol_type
                else:
                    expected_serial.protocol_type = "raw"
            expected_serial.target_port = serial_dev.target_port
            if serial_elem is None:
                test.fail("Expect exist serial device, "
                          "but found none.")
            cur_serial = serial_cls.new_from_element(serial_elem)
            if target_type == 'pci-serial':
                if cur_serial.address is None:
                    test.fail("Expect serial device address is not assigned")
                else:
                    logging.debug("Serial address is: %s", cur_serial.address)

            logging.debug("Expected serial XML is:\n%s", expected_serial)
            logging.debug("Current serial XML is:\n%s", cur_serial)
            # Compare current serial and console with oracle.
            if (target_type != 'pci-serial' and machine_type != 'pseries'
                    and not expected_serial == cur_serial):
                # "==" has been override
                test.fail("Expect serial device:\n%s\nBut got:\n "
                          "%s" % (expected_serial, cur_serial))

    def prepare_serial_console():
        """
        Prepare serial console to connect with guest serial according to
        the serial type
        """
        is_server = console_type == 'server'
        if serial_type == 'unix':
            # Unix socket path should match SELinux label
            socket_path = '/var/lib/libvirt/qemu/virt-test'
            console = Console('unix', socket_path, is_server)
        elif serial_type == 'tls':
            host = '127.0.0.1'
            service = 5556
            console = Console('tls', (host, service), is_server,
                              custom_pki_path)
        elif serial_type == 'tcp':
            host = '127.0.0.1'
            service = 2445
            console = Console('tcp', (host, service), is_server)
        elif serial_type == 'udp':
            host = '127.0.0.1'
            service = 2445
            console = Console('udp', (host, service), is_server)
        elif serial_type == 'file':
            socket_path = '/var/lib/libvirt/virt-test'
            console = Console('file', socket_path, is_server)
        elif serial_type == 'pipe':
            socket_path = '/tmp/virt-test'
            console = Console('pipe', socket_path, is_server)
        else:
            logging.debug("Serial type %s don't support console test yet.",
                          serial_type)
            console = None
        return console

    def check_qemu_cmdline():
        """
        Check if VM's qemu command line meets expectation.
        """
        cmdline = open('/proc/%s/cmdline' % vm.get_pid()).read()
        logging.debug('Qemu command line: %s', cmdline)
        options = cmdline.split('\x00')
        ser_opt = None
        con_opt = None
        ser_dev = None
        con_dev = None
        exp_ser_opt = None
        exp_ser_dev = None
        exp_con_opt = None
        exp_con_dev = None
        # Get serial and console options from qemu command line
        for idx, opt in enumerate(options):
            if opt == '-chardev':
                if 'id=charserial' in options[idx + 1]:
                    ser_opt = options[idx + 1]
                    ser_dev = options[idx + 3]
                    logging.debug('Serial option is: %s', ser_opt)
                    logging.debug('Serial device option is: %s', ser_dev)
                if 'id=charconsole' in options[idx + 1]:
                    con_opt = options[idx + 1]
                    con_dev = options[idx + 3]
                    logging.debug('Console option is: %s', con_opt)
                    logging.debug('Console device option is: %s', con_dev)

        # Get expected serial and console options from params
        if serial_type == 'dev':
            ser_type = 'tty'
        elif serial_type in ['unix', 'tcp', 'tls']:
            ser_type = 'socket'
        else:
            ser_type = serial_type

        exp_ser_opts = [ser_type, 'id=charserial0']
        if serial_type in ['dev', 'file', 'pipe', 'unix']:
            for source in serial_dev.sources:
                if 'path' in source:
                    path = source['path']
            if serial_type == 'file':
                # Use re to make this fdset number flexible
                exp_ser_opts.append(r'path=/dev/fdset/\d+')
                exp_ser_opts.append('append=on')
            elif serial_type == 'unix':
                # Use re to make this fd number flexible
                exp_ser_opts.append(r'fd=\d+')
            else:
                exp_ser_opts.append('path=%s' % path)
        elif serial_type in ['tcp', 'tls']:
            for source in serial_dev.sources:
                if 'host' in source:
                    host = source['host']
                if 'service' in source:
                    port = source['service']
            exp_ser_opts.append('host=%s' % host)
            exp_ser_opts.append('port=%s' % port)
        elif serial_type in ['udp']:
            localaddr = ''
            localport = '0'
            for source in serial_dev.sources:
                if source['mode'] == 'connect':
                    if 'host' in source:
                        host = source['host']
                    if 'service' in source:
                        port = source['service']
                else:
                    if 'host' in source:
                        localaddr = source['host']
                    if 'service' in source:
                        localport = source['service']
            exp_ser_opts.append('host=%s' % host)
            exp_ser_opts.append('port=%s' % port)
            exp_ser_opts.append('localaddr=%s' % localaddr)
            exp_ser_opts.append('localport=%s' % localport)

        if serial_type in ['unix', 'tcp', 'udp', 'tls']:
            for source in serial_dev.sources:
                if 'mode' in source:
                    mode = source['mode']
            if mode == 'bind':
                exp_ser_opts.append('server')
                exp_ser_opts.append('nowait')

        if serial_type == 'tls':
            exp_ser_opts.append('tls-creds=objcharserial0_tls0')

        exp_ser_opt = ','.join(exp_ser_opts)

        arch = platform.machine()
        if "ppc" in arch:
            exp_ser_devs = ['spapr-vty', 'chardev=charserial0',
                            'reg=0x30000000']
            if libvirt_version.version_compare(3, 9, 0):
                exp_ser_devs.insert(2, 'id=serial0')
        elif "s390x" in arch:
            dev_type = 'sclpconsole'
            if target_model:
                dev_type = target_model
            exp_ser_devs = [dev_type, 'chardev=charserial0',
                            'id=serial0']
        elif "aarch64" in arch:
            exp_ser_devs = ['chardev:charserial0']
        else:
            logging.debug('target_type: %s', target_type)
            if target_type == 'pci-serial':
                exp_ser_devs = ['pci-serial', 'chardev=charserial0',
                                'id=serial0', r'bus=pci.\d+', r'addr=0x\d+']
            else:
                exp_ser_devs = ['isa-serial', 'chardev=charserial0',
                                'id=serial0']
        exp_ser_dev = ','.join(exp_ser_devs)

        if console_target_type != 'serial' or serial_type == 'spicevmc':
            exp_ser_opt = None
            exp_ser_dev = None
        logging.debug("exp_ser_opt: %s", exp_ser_opt)
        logging.debug("ser_opt: %s", ser_opt)

        # Check options against expectation
        if exp_ser_opt is not None and re.match(exp_ser_opt, ser_opt) is None:
            test.fail('Expect get qemu command serial option "%s", '
                      'but got "%s"' % (exp_ser_opt, ser_opt))
        if exp_ser_dev is not None and ser_dev is not None \
           and re.match(exp_ser_dev, ser_dev) is None:
            test.fail(
                'Expect get qemu command serial device option "%s", '
                'but got "%s"' % (exp_ser_dev, ser_dev))

        if console_target_type == 'virtio':
            exp_con_opts = [serial_type, 'id=charconsole1']
            exp_con_opt = ','.join(exp_con_opts)

            exp_con_devs = []
            if console_target_type == 'virtio':
                exp_con_devs.append('virtconsole')
            exp_con_devs += ['chardev=charconsole1', 'id=console1']
            exp_con_dev = ','.join(exp_con_devs)
            if con_opt != exp_con_opt:
                test.fail(
                    'Expect get qemu command console option "%s", '
                    'but got "%s"' % (exp_con_opt, con_opt))
            if con_dev != exp_con_dev:
                test.fail(
                    'Expect get qemu command console device option "%s", '
                    'but got "%s"' % (exp_con_dev, con_dev))

    def check_serial_console(console, username, password):
        """
        Check whether console works properly by read the result for read only
        console and login and emit a command for read write console.
        """
        if serial_type in ['file', 'tls']:
            _, output = console.read_until_output_matches(['.*[Ll]ogin:'])
        else:
            console.wait_for_login(username, password)
            status, output = console.cmd_status_output('pwd')
            logging.debug("Command status: %s", status)
            logging.debug("Command output: %s", output)

    def cleanup(objs_list):
        """
        Clean up test environment
        """
        if serial_type == 'file':
            if os.path.exists('/var/lib/libvirt/virt-test'):
                os.remove('/var/lib/libvirt/virt-test')

        # recovery test environment
        for obj in objs_list:
            obj.auto_recover = True
            del obj

    def get_console_type():
        """
        Check whether console should be started before or after guest starting.
        """
        if serial_type in ['tcp', 'unix', 'udp', 'tls']:
            for source in serial_dev.sources:
                if 'mode' in source and source['mode'] == 'connect':
                    return 'server'
            return 'client'
        elif serial_type in ['file']:
            return 'client'
        elif serial_type in ['pipe']:
            return 'server'

    serial_type = params.get('serial_dev_type', 'pty')
    sources_str = params.get('serial_sources', '')
    username = params.get('username')
    password = params.get('password')
    console_target_type = params.get('console_target_type', 'serial')
    target_type = params.get('target_type', 'isa-serial')
    target_model = params.get('target_model', '')
    console_target_port = params.get('console_target_port', '0')
    second_serial_console = params.get('second_serial_console', 'no') == 'yes'
    custom_pki_path = params.get('custom_pki_path', '/etc/pki/libvirt-chardev')
    auto_recover = params.get('auto_recover', 'no')
    client_pwd = params.get('client_pwd', None)
    server_pwd = params.get('server_pwd', None)
    machine_type = params.get('machine_type', '')
    remove_devices = params.get('remove_devices', 'serial,console')
    if remove_devices:
        remove_devices = remove_devices.split(',')
    args_list = [client_pwd, server_pwd]

    for arg in args_list:
        if arg and arg.count("ENTER.YOUR."):
            raise test.fail("Please assign a value for %s!" % arg)

    vm_name = params.get('main_vm', 'virt-tests-vm1')
    vm = env.get_vm(vm_name)

    # it's used to clean up TLS objs later
    objs_list = []

    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        if remove_devices:
            for device_type in remove_devices:
                vm_xml.remove_all_device_by_type(device_type)
        if serial_type == "tls":
            test_dict = dict(params)
            tls_obj = TLSConnection(test_dict)
            if auto_recover == "yes":
                objs_list.append(tls_obj)
            tls_obj.conn_setup(False, True)

        serial_dev = prepare_serial_device()
        if console_target_type == 'serial' or second_serial_console:
            logging.debug('Serial device:\n%s', serial_dev)
            vm_xml.add_device(serial_dev)
            if serial_type == "spiceport":
                spice_graphics_dev = prepare_spice_graphics_device()
                logging.debug('Spice graphics device:\n%s', spice_graphics_dev)
                vm_xml.add_device(spice_graphics_dev)

        console_dev = prepare_console_device()
        logging.debug('Console device:\n%s', console_dev)
        vm_xml.add_device(console_dev)
        if second_serial_console:
            console_target_type = 'serial'
            console_target_port = '1'
            console_dev = prepare_console_device()
            logging.debug('Console device:\n%s', console_dev)
            vm_xml.add_device(console_dev)
            vm_xml.undefine()
            res = virsh.define(vm_xml.xml)
            if res.stderr.find(r'Only the first console can be a serial port'):
                logging.debug("Test only the first console can be a serial"
                              "succeeded")
                return
            test.fail("Test only the first console can be serial failed.")

        console_type = get_console_type()

        if not define_and_check():
            logging.debug("Can't define the VM, exiting.")
            return
        if console_target_type != 'virtio':
            check_xml()

        expected_fails = []
        if serial_type == 'nmdm' and platform.system() != 'FreeBSD':
            expected_fails.append("unsupported chardev 'nmdm'")

        # Prepare console before start when console is server
        if console_type == 'server':
            console = prepare_serial_console()

        res = virsh.start(vm_name)
        libvirt.check_result(res, expected_fails, [])
        if res.exit_status:
            logging.debug("Can't start the VM, exiting.")
            return

        # Prepare console after start when console is client
        if console_type == 'client':
            console = prepare_serial_console()

        if (console_type is not None and serial_type != 'tls' and
           console_type != 'server'):
            check_serial_console(console, username, password)

        vm.destroy()

    finally:
        cleanup(objs_list)
        vm_xml_backup.sync()
