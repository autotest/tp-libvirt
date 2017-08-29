import os
import platform
import logging
import time
import errno
import socket
import threading
import select

import aexpect

from autotest.client.shared import error

from virttest import remote
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices import librarian


class Console(aexpect.ShellSession):

    """
    This class create a socket or file/pipe descriptor for console connect
    with qemu VM in different ways.
    """

    def __init__(self, console_type, address, is_server=False):
        """
        Initialize the instance and create socket/fd for connect with.

        :param console_type: Type of console to connect with. Supports
                             unix, tcp, udp, file or pipe
        :param address: Address for socket or fd. For tcp and udp, This should
                        be a tuple of (host, port). For unix, file, pipe
                        this should be a string representing the path to be
                        connect with
        :param is_server: Whether this connection act as a server or a client
        """
        self.exit = False
        self.address = address
        self.peer_addr = None
        self.console_type = console_type
        self.socket = None
        self.read_fd = None
        self.write_fd = None
        self._poll_thread = None
        self.linesep = "\n"
        self.prompt = r"^\[.*\][\#\$]\s*$"
        self.status_test_command = "echo $?"

        if console_type == 'unix':
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket.connect(address)
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
            self.read_fd = os.open(
                address, os.O_RDONLY | os.O_CREAT | os.O_NONBLOCK)

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
                    data += new_data
                    return data
                elif self.console_type == 'udp':
                    new_data, self.peer_addr = self.socket.recvfrom(
                        1024, socket.MSG_DONTWAIT)
                    data += new_data
                    return data
                elif self.console_type in ['file', 'pipe']:
                    try:
                        read_fds, _, _ = select.select(
                            [self.read_fd], [], [], internal_timeout)
                    except Exception:
                        return data
                    if self.read_fd in read_fds:
                        new_data = os.read(self.read_fd, 1024)
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
            self.socket.sendall(cont)
        elif self.console_type == 'udp':
            if self.peer_addr is None:
                logging.debug("No peer connection yet.")
            else:
                self.socket.sendto(cont, self.peer_addr)
        elif self.console_type == 'pipe':
            try:
                os.write(self.write_fd, cont)
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
            os.close(self.read_fd)
        if self.write_fd is not None:
            os.close(self.write_fd)
        if self.console_type == 'pipe':
            if os.path.exists(self.address + '.in'):
                os.remove(self.address + '.in')
            if os.path.exists(self.address + '.out'):
                os.remove(self.address + '.out')


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
    def prepare_serial_device():
        """
        Prepare a serial device XML according to parameters
        """
        serial = librarian.get('serial')(serial_type)

        serial.target_port = "0"

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
        console = librarian.get('console')(serial_type)
        console.target_type = console_target_type

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

        # Predict expected serial and console XML
        expected_console = console_cls(serial_type)

        if serial_type == 'udp':
            sources = []
            for source in serial_dev.sources:
                if 'service' in source and 'mode' not in source:
                    source['mode'] = 'connect'
                sources.append(source)
        else:
            sources = serial_dev.sources

        expected_console.sources = sources

        if serial_type == 'tcp':
            if 'protocol_type' in serial_type:
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
            raise error.TestFail("Expect generate console automatically, "
                                 "but found none.")
        if serial_elem and console_target_type != 'serial':
            raise error.TestFail("Don't Expect exist serial device, "
                                 "but found:\n%s" % serial_elem)

        cur_console = console_cls.new_from_element(console_elem)
        logging.debug("Current console XML is:\n%s", cur_console)
        # Compare current serial and console with oracle.
        if not expected_console == cur_console:
            raise error.TestFail("Expect generate console:\n%s\nBut got:\n%s" %
                                 (expected_console, cur_console))

        if console_target_type == 'serial':
            serial_cls = librarian.get('serial')
            expected_serial = serial_cls(serial_type)
            expected_serial.sources = sources

            if serial_type == 'tcp':
                if 'protocol_type' in serial_type:
                    expected_serial.protocol_type = serial_dev.protocol_type
                else:
                    expected_serial.protocol_type = "raw"
            expected_serial.target_port = serial_dev.target_port
            if serial_elem is None:
                raise error.TestFail("Expect exist serial device, "
                                     "but found none.")
            cur_serial = serial_cls.new_from_element(serial_elem)
            logging.debug("Expected serial XML is:\n%s", expected_serial)
            logging.debug("Current serial XML is:\n%s", cur_serial)
            # Compare current serial and console with oracle.
            if not expected_serial == cur_serial:
                raise error.TestFail("Expect serial device:\n%s\nBut got:\n"
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
        elif serial_type == 'tcp':
            host = '127.0.0.1'
            service = 2445
            console = Console('tcp', (host, service), is_server)
        elif serial_type == 'udp':
            host = '127.0.0.1'
            service = 2445
            console = Console('udp', (host, service), is_server)
        elif serial_type == 'file':
            socket_path = '/var/log/libvirt/virt-test'
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
        elif serial_type in ['unix', 'tcp']:
            ser_type = 'socket'
        else:
            ser_type = serial_type

        exp_ser_opts = [ser_type, 'id=charserial0']
        if serial_type in ['dev', 'file', 'pipe', 'unix']:
            for source in serial_dev.sources:
                if 'path' in source:
                    path = source['path']
            if serial_type == 'file':
                # This fdset number should be make flexible later
                exp_ser_opts.append('path=/dev/fdset/2')
                exp_ser_opts.append('append=on')
            else:
                exp_ser_opts.append('path=%s' % path)
        elif serial_type in ['tcp']:
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

        if serial_type in ['unix', 'tcp', 'udp']:
            for source in serial_dev.sources:
                if 'mode' in source:
                    mode = source['mode']
            if mode == 'bind':
                exp_ser_opts.append('server')
                exp_ser_opts.append('nowait')

        exp_ser_opt = ','.join(exp_ser_opts)

        exp_ser_devs = ['isa-serial', 'chardev=charserial0', 'id=serial0']
        exp_ser_dev = ','.join(exp_ser_devs)

        if console_target_type != 'serial' or serial_type in ['spiceport']:
            exp_ser_opt = None
            exp_ser_dev = None

        # Check options against expectation
        if ser_opt != exp_ser_opt:
            raise error.TestFail('Expect get qemu command serial option "%s", '
                                 'but got "%s"' % (exp_ser_opt, ser_opt))
        if ser_dev != exp_ser_dev:
            raise error.TestFail(
                'Expect get qemu command serial device option "%s", '
                'but got "%s"' % (exp_ser_dev, ser_dev))

        if console_target_type == 'virtio':
            exp_con_opts = [serial_type, 'id=charconsole0']
            exp_con_opt = ','.join(exp_con_opts)

            exp_con_devs = []
            if console_target_type == 'virtio':
                exp_con_devs.append('virtconsole')
            exp_con_devs += ['chardev=charconsole0', 'id=console0']
            exp_con_dev = ','.join(exp_con_devs)
            if con_opt != exp_con_opt:
                raise error.TestFail(
                    'Expect get qemu command console option "%s", '
                    'but got "%s"' % (exp_con_opt, con_opt))
            if con_dev != exp_con_dev:
                raise error.TestFail(
                    'Expect get qemu command console device option "%s", '
                    'but got "%s"' % (exp_con_dev, con_dev))

    def check_serial_console(console, username, password):
        """
        Check whether console works properly by read the result for read only
        console and login and emit a command for read write console.
        """
        if serial_type in ['file']:
            _, output = console.read_until_output_matches(['.*[Ll]ogin:'])
        else:
            console.wait_for_login(username, password)
            status, output = console.cmd_status_output('pwd')
            logging.debug("Command status: %s", status)
            logging.debug("Command output: %s", output)

    def check_cleanup():
        """
        Clean up residue file.
        """
        if serial_type == 'file':
            if os.path.exists('/var/log/libvirt/virt-test'):
                os.remove('/var/log/libvirt/virt-test')

    def get_console_type():
        """
        Check whether console should be started before or after guest starting.
        """
        if serial_type in ['tcp', 'unix', 'udp']:
            for source in serial_dev.sources:
                if 'mode' in source and source['mode'] == 'connect':
                    return 'server'
            return 'client'
        elif serial_type in ['file']:
            return 'client'
        elif serial_type in ['pipe']:
            return 'server'

    serial_type = params.get('serial_type', 'pty')
    sources_str = params.get('serial_sources', '')
    username = params.get('username')
    password = params.get('password')
    console_target_type = params.get('console_target_type', 'serial')
    vm_name = params.get('main_vm', 'virt-tests-vm1')
    vm = env.get_vm(vm_name)

    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        vm_xml.remove_all_device_by_type('serial')
        vm_xml.remove_all_device_by_type('console')
        serial_dev = prepare_serial_device()
        if console_target_type == 'serial':
            logging.debug('Serial device:\n%s', serial_dev)
            vm_xml.add_device(serial_dev)
        console_dev = prepare_console_device()
        logging.debug('Console device:\n%s', console_dev)
        vm_xml.add_device(console_dev)

        console_type = get_console_type()

        if not define_and_check():
            logging.debug("Can't define the VM, exiting.")
            return

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

        check_qemu_cmdline()

        # Prepare console after start when console is client
        if console_type == 'client':
            console = prepare_serial_console()

        if console_type is not None:
            check_serial_console(console, username, password)

        vm.destroy()

        check_cleanup()
    finally:
        vm_xml_backup.sync()
