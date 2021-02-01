import os
import time
import signal
import logging

from avocado.utils import process

from virttest import utils_config
from virttest import utils_split_daemons
from virttest.utils_libvirtd import Libvirtd


def run(test, params, env):
    """
    Kill libvirt daemon with different signals and check
    whether daemon restart properly and leaving no pid file
    if stopped.
    """
    def get_pid(libvirtd):
        """
        Get the pid of libvirt daemon process.
        """
        pid = int(open(pid_file).read())
        return pid

    def send_signal(pid, signal_name):
        """
        Send signal to a process by pid.
        """
        signal_num = getattr(signal, signal_name)
        os.kill(pid, signal_num)

    def start_mark(src_file, dest_file):
        """
        Copy the src_file to a tmp file
        :param src_file: The file should be checked.
        :param dest_file: The temp file to mark the time point.
        """
        # Clean the dest file if existed
        if os.path.exists(dest_file):
            os.remove(dest_file)
        cmdline = 'cp %s %s' % \
                  (src_file, dest_file)
        process.run(cmdline, shell=True)

    pid_file = '/var/run/libvirtd.pid'
    if utils_split_daemons.is_modular_daemon():
        pid_file = '/var/run/virtqemud.pid'
    message_src_file = '/var/log/messages'
    message_dest_file = '/tmp/messages_tmp'
    signal_name = params.get("signal", "SIGTERM")
    should_restart = params.get("expect_restart", "yes") == "yes"
    timeout = int(params.get("restart_timeout", 1))
    pid_should_change = params.get("expect_pid_change", "yes") == "yes"
    sysconfig = params.get("sysconfig", None)
    check_dmesg = params.get("check_dmesg", None)

    libvirtd = Libvirtd("virtqemud")
    try:
        libvirtd.start()

        if sysconfig:
            config = utils_config.LibvirtdSysConfig()
            setattr(config, sysconfig.split('=')[0], sysconfig.split('=')[1])
            libvirtd.restart()
        if check_dmesg:
            start_mark(message_src_file, message_dest_file)

        pid = get_pid(libvirtd)
        logging.debug("Pid of libvirtd is %d" % pid)

        logging.debug("Killing process %s with %s" % (pid, signal_name))
        send_signal(pid, signal_name)

        # Wait for libvirtd to restart or reload
        time.sleep(timeout)

        if libvirtd.is_running():
            if not should_restart:
                test.fail(
                    "libvirtd should stop running after signal %s"
                    % signal_name)
            new_pid = get_pid(libvirtd)
            logging.debug("New pid of libvirtd is %d" % new_pid)
            if pid == new_pid and pid_should_change:
                test.fail("Pid should have been changed.")
            if pid != new_pid and not pid_should_change:
                test.fail("Pid should not have been changed.")
        else:
            if should_restart:
                test.fail(
                    "libvirtd should still running after signal %s"
                    % signal_name)

        if check_dmesg:
            cmdline = 'diff %s %s' % \
                      (message_src_file, message_dest_file)
            res = process.run(cmdline, shell=True, ignore_status=True).stdout_text
            if check_dmesg not in res:
                test.fail('%s should in %s , but not now' % (check_dmesg, message_src_file))

    finally:
        if not libvirtd.is_running():
            if os.path.exists(pid_file):
                os.remove(pid_file)
                libvirtd.start()
                test.fail("Pid file should not reside")
            libvirtd.start()
        if sysconfig:
            config.restore()
            libvirtd.restart()
