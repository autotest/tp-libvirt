import os
import time
import logging

from avocado.utils import process

from virttest import virsh
from virttest import utils_config
from virttest import utils_split_daemons
from virttest import virt_admin
from virttest.utils_libvirtd import LibvirtdSession
from virttest.utils_libvirtd import Libvirtd

from virttest import libvirt_version


def run(test, params, env):
    """
    Start libvirt daemon with different options.
    Check socket files.
    """
    log = []

    def _logger(line):
        """
        Callback function to log libvirtd output.
        """
        log.append(line)

    def check_help(params):
        """
        Check whether the output is help and meets expectation
        """
        expected_help = params.get('expected_help', 'no') == 'yes'
        is_help = any(line.startswith('Usage:') for line in log)
        if expected_help != is_help:
            test.fail(
                'Expected output help is %s, but get output:\n%s' %
                (expected_help, '\n'.join(log)))

    def check_version(params):
        """
        Check whether the output is libvirtd version.
        """
        expected_version = params.get('expected_version', 'no') == 'yes'
        is_version = log[0].startswith('{} (libvirt)'.format(daemon_name))
        if expected_version != is_version:
            test.fail(
                'Expected output version is %s, but get output:\n%s' %
                (expected_version, '\n'.join(log)))

    def check_unix_socket_files():
        """
        Check whether the socket file exists.
        """
        rw_sock_path = '/var/run/libvirt/libvirt-sock'
        ro_sock_path = '/var/run/libvirt/libvirt-sock-ro'

        if libvirtd.running or libvirt_version.version_compare(5, 6, 0):
            if not os.path.exists(rw_sock_path):
                test.fail('RW unix socket file not found at %s' %
                          rw_sock_path)
            if not os.path.exists(ro_sock_path):
                test.fail('RO unix socket file not found at %s' %
                          ro_sock_path)
        else:
            if os.path.exists(rw_sock_path) or os.path.exists(ro_sock_path):
                test.fail('Expect unix socket file do not exists '
                          'when libvirtd is stopped')

    def check_pid_file():
        """
        Check whether the pid file exists.
        """
        if not os.path.exists(pid_path):
            test.fail("PID file not found at %s" % pid_path)

        with open(pid_path) as pid_file:
            pid = int(pid_file.readline())
        result = process.run('pgrep %s' % daemon_name,
                             ignore_status=True, shell=True)
        expected_pid = int(result.stdout_text.strip().split()[0])

        if pid != expected_pid:
            test.fail("PID file content mismatch. Expected %s "
                      "but got %s" % (expected_pid, pid))

    def check_config_file():
        """
        Check whether the config file take effects by checking max_clients.
        """
        connect_uri = daemon_name + ":///system"
        vp = virt_admin.VirtadminPersistent(uri=connect_uri)
        result = vp.srv_clients_info(daemon_name, uri=connect_uri, ignore_status=True, debug=True)
        output = result.stdout.strip().splitlines()
        out_split = [item.split(':') for item in output]
        out_dict = dict([[item[0].strip(), item[1].strip()] for item in out_split])
        if int(out_dict["nclients_max"]) != check_max_clients:
            test.fail('Expected max_clients is %s, but got %s' %
                      (check_max_clients, out_dict["nclients_max"]))

    MAX_TIMEOUT = 10
    arg_str = params.get("libvirtd_arg", "")
    time_tolerance = float(params.get("exit_time_tolerance", 1))
    expected_exit_time = float(params.get("expected_exit_time", 'inf'))
    config_path = params.get('expected_config_path', "")
    pid_path = params.get('expected_pid_path', "")
    daemon_name = params.get('daemon_name', "")
    test_config = params.get('test_config', "no") == "yes"
    require_modular_daemon = params.get('require_modular_daemon', "no") == "yes"

    utils_split_daemons.daemon_mode_check(require_modular_daemon)
    if expected_exit_time == float('inf'):
        timeout = MAX_TIMEOUT
    else:
        if expected_exit_time > 0:
            if len(virsh.dom_list('--name').stdout.strip().splitlines()):
                test.cancel('Timeout option will be ignore if '
                            'there exists living domain')
        timeout = expected_exit_time + time_tolerance

    libvirtd = LibvirtdSession(service_name=daemon_name, logging_handler=_logger)

    # Setup config file.
    check_max_clients = int(101)
    if test_config:
        config = utils_config.get_conf_obj(daemon_name)
        logging.debug(config.conf_path)
        config_path = config.conf_path
        config.max_clients = check_max_clients
        arg_str = arg_str + config_path

    try:
        check_unix_socket_files()

        Libvirtd(daemon_name).stop()
        libvirtd.start(arg_str=arg_str, wait_for_working=False)

        start = time.time()
        libvirtd_exited = libvirtd.wait_for_stop(timeout=timeout, step=0.1)
        wait_time = time.time() - start

        if log:
            logging.debug("Libvirtd log:")
            for line in log:
                logging.debug(line)

            check_help(params)
            check_version(params)

        if libvirtd_exited:
            if expected_exit_time == float('inf'):
                test.fail("Expected never stop, but ran %ss" % wait_time)
            elif wait_time < expected_exit_time - time_tolerance:
                test.fail("Expected exit in %ss(+-%ss), but ran %ss" %
                          (expected_exit_time, time_tolerance, wait_time))
        else:
            if expected_exit_time != float('inf'):
                test.fail("Expected exit in %ss(+-%ss), but ran timeout in %ss" %
                          (expected_exit_time, time_tolerance, wait_time))

        not libvirt_version.version_compare(5, 6, 0) and check_unix_socket_files()
        if test_config:
            check_config_file()
        if pid_path:
            check_pid_file()
    finally:
        libvirtd.exit()
        Libvirtd(daemon_name).stop()
        socket_name = daemon_name + ".socket"
        Libvirtd(socket_name).restart()
        Libvirtd(daemon_name).start()

        # Clean up config file
        if test_config:
            config.restore()
        if os.path.exists(pid_path):
            os.remove(pid_path)
