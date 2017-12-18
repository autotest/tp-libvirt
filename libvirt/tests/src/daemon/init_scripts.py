import os
import glob
import shutil
import logging

from autotest.client import os_dep
from autotest.client import utils
from autotest.client.shared import error

from virttest import utils_libvirtd


def run(test, params, env):
    """
    Start, restart, reload and stop libvirt daemon with all possible
    init scripts.
    """
    def service_avail(cmd):
        """
        Check the availability of three init services.

        :param cmd: service name. Can be initctl, systemctl or initscripts
        :return: True if init system avaiable or False if not.
        """
        if cmd in ['initctl', 'systemctl']:
            try:
                os_dep.command(cmd)
                return True
            except ValueError:
                return False
        elif cmd == 'initscripts':
            return os.path.exists('/etc/rc.d/init.d/libvirtd')

    def service_check(cmd):
        """
        Check the availability of libvirtd init scripts.

        :param cmd: service name. Can be initctl, systemctl or initscripts
        """
        if cmd == 'systemctl':
            res = utils.run('systemctl list-unit-files')
            for ufile in ["libvirt-guests.service",
                          "libvirtd.service", "libvirtd.socket"]:
                if ufile not in res.stdout:
                    raise error.TestFail('Missing systemd unit file %s'
                                         % ufile)
        elif cmd == 'initctl':
            script = glob.glob('/usr/share/doc/*/libvirtd.upstart')
            if not script:
                raise error.TestFail('Cannot find libvirtd.upstart script')
            if not os.path.exists('/etc/init/libvirtd.conf'):
                shutil.copyfile(script[0], '/etc/init/libvirtd.conf')

    def service_call(cmd, action, expected_exit_code=0, user=None):
        """
        Call a specific action using different init system and check
        the exit code against expectation.

        :param cmd: service name. Can be initctl, systemctl or initscripts
        :param action: action name. Such as start, stop or restart
        :param expected_exit_code: Expected return code of the command
        :param user: username service call to run as. None if run as root
        :return: CmdResult instance
        """
        logging.debug("%s libvirtd using %s" % (action, cmd))
        if cmd in ['initctl', 'systemctl']:
            cmdline = "%s %s libvirtd" % (cmd, action)
        elif cmd == 'initscripts':
            cmdline = "/etc/rc.d/init.d/libvirtd %s" % action

        if user:
            cmdline = 'su - %s -c "%s"' % (user, cmdline)

        res = utils.run(cmdline, ignore_status=True)
        logging.debug(res)

        if res.exit_status != expected_exit_code:
            raise error.TestFail("Expected exit status is %s, but got %s." %
                                 (expected_exit_code, res.exit_status))
        return res

    commands = ['initctl', 'initscripts', 'systemctl']
    avail_commands = []
    for command in commands:
        if service_avail(command):
            avail_commands.append(command)
    logging.debug("Available commands: %s" % avail_commands)

    libvirtd = utils_libvirtd.Libvirtd()
    libvirtd.stop()

    username = 'virt-test'
    utils.run('useradd %s' % username, ignore_status=True)
    utils.run('usermod -s /bin/bash %s' % username, ignore_status=True)
    try:
        for command in avail_commands:
            if command == 'systemctl':
                service_call(command, 'start',
                             user=username,
                             expected_exit_code=1)
            service_call(command, 'start')
            service_call(command, 'status')

            if command == 'systemctl':
                service_call(command, 'reload',
                             user=username,
                             expected_exit_code=1)
            service_call(command, 'reload')
            service_call(command, 'status')

            if command == 'systemctl':
                service_call(command, 'restart',
                             user=username,
                             expected_exit_code=1)
            service_call(command, 'restart')
            service_call(command, 'status')

            if command == 'systemctl':
                service_call(command, 'stop',
                             user=username,
                             expected_exit_code=1)
            service_call(command, 'stop')
            if command == 'initctl':
                service_call(command, 'status')
            else:
                service_call(command, 'status',
                             expected_exit_code=3)
    finally:
        utils.run('userdel -r virt-test', ignore_status=True)
        libvirtd.restart()
