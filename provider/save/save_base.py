import logging
import os

from avocado.core import exceptions
from avocado.utils import process

LOG = logging.getLogger('avocado.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def pre_save_setup(vm, serial=False):
    """
    Setup vm before save:
    Start ping process and get uptime since when on vm

    :param vm: vm instance
    :param serial: Whether to use a serial connection
    :return: a tuple of pid of ping and uptime since when
    """
    if serial:
        vm.cleanup_serial_console()
        vm.create_serial_console()
        session = vm.wait_for_serial_login()
    else:
        session = vm.wait_for_login()
    upsince = session.cmd_output('uptime --since').strip()
    LOG.debug(f'VM has been up since {upsince}')
    ping_cmd = 'ping 127.0.0.1 >/tmp/ping_out 2>&1'
    # This session shouldn't be closed or it will kill ping
    session.sendline(ping_cmd + '&')
    pid_ping = session.cmd_output('pidof ping').strip().split()[-1]
    LOG.debug(f'Pid of ping: {pid_ping}')

    return pid_ping, upsince


def post_save_check(vm, pid_ping, upsince, serial=False):
    """
    Check vm status after save-restore:
    Whether ping is still running, uptime since when is the same as before
    save-restore.

    :param vm: vm instance
    :param pid_ping: pid of ping
    :param upsince: uptime since when
    :param serial: Whether to use a serial connection
    """
    if serial:
        vm.cleanup_serial_console()
        vm.create_serial_console()
        session = vm.wait_for_serial_login()
    else:
        session = vm.wait_for_login()
    upsince_restore = session.cmd_output('uptime --since').strip()
    LOG.debug(f'VM has been up (after restore) since {upsince_restore}')
    LOG.debug(session.cmd_output(f'pidof ping'))
    proc_info = session.cmd_output(f'ps -p {pid_ping} -o command').strip()
    LOG.debug(proc_info)
    LOG.debug(session.cmd_output(f'ps -ef|grep ping'))
    session.close()

    if upsince_restore != upsince:
        LOG.warning(f'Uptime since {upsince_restore} is '
                    f'incorrect, should be {upsince}')
    ping_cmd = 'ping 127.0.0.1'
    if ping_cmd not in proc_info:
        raise exceptions.TestFail('Cannot find running ping command '
                                  'after save-restore.')


def check_ownership(path, uid, gid):
    """
    Check whether ownership of path meets expectation

    :param path: path to check
    :param uid: expected uid
    :param gid: expected gid
    :param test: test instance
    """
    process.run(f'ls -lZ {path}', shell=True)
    stat = os.stat(path)
    LOG.debug(f'Path stat info: {stat}')
    if (stat.st_uid, stat.st_gid) != (uid, gid):
        raise exceptions.TestFail(f'File ownership not correct, '
                                  f'should be {uid, gid}, '
                                  f'not {stat.st_uid, stat.st_gid}')
