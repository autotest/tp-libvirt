import os
import time
import signal
import logging
from autotest.client.shared import error
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

    pid_file = '/var/run/libvirtd.pid'
    signal_name = params.get("signal", "SIGTERM")
    should_restart = params.get("expect_restart", "yes") == "yes"
    pid_should_change = params.get("expect_pid_change", "yes") == "yes"

    libvirtd = Libvirtd()
    try:
        libvirtd.start()

        pid = get_pid(libvirtd)
        logging.debug("Pid of libvirtd is %d" % pid)

        logging.debug("Killing process %s with %s" % (pid, signal_name))
        send_signal(pid, signal_name)

        # Wait for libvirtd to restart or reload
        time.sleep(1)

        if libvirtd.is_running():
            if not should_restart:
                raise error.TestFail(
                    "libvirtd should stop running after signal %s"
                    % signal_name)
            new_pid = get_pid(libvirtd)
            logging.debug("New pid of libvirtd is %d" % new_pid)
            if pid == new_pid and pid_should_change:
                raise error.TestFail("Pid should have been changed.")
            if pid != new_pid and not pid_should_change:
                raise error.TestFail("Pid should not have been changed.")
        else:
            if should_restart:
                raise error.TestFail(
                    "libvirtd should still running after signal %s"
                    % signal_name)

    finally:
        if not libvirtd.is_running():
            if os.path.exists(pid_file):
                os.remove(pid_file)
                libvirtd.start()
                raise error.TestFail("Pid file should not reside")
            libvirtd.start()
