import logging

LOG = logging.getLogger('avocado.' + __name__)
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def pre_save_setup(vm):
    """
    Setup vm before save:
    Start ping process and get uptime since when on vm

    :param vm: vm instance
    :return: a tuple of pid of ping and uptime since when
    """
    session = vm.wait_for_login()
    upsince = session.cmd_output('uptime --since').strip()
    LOG.debug(f'VM has been up since {upsince}')
    ping_cmd = 'ping 127.0.0.1'
    session.sendline(ping_cmd + '&')
    session.sendline()
    pid_ping = session.cmd_output('pidof ping').strip().split()[-1]
    # The session shouldn't be closed or it will kill ping
    LOG.debug(f'Pid of ping: {pid_ping}')

    return pid_ping, upsince


def post_save_check(vm, pid_ping, upsince):
    """
    Check vm status after save-restore:
    Whether ping is still running, uptime since when is the same as before
    save-restore.

    :param vm: vm instance
    :param pid_ping: pid of ping
    :param upsince: uptime since when
    """
    session = vm.wait_for_login()
    upsince_restore = session.cmd_output('uptime --since').strip()
    LOG.debug(f'VM has been up (after restore) since {upsince_restore}')
    LOG.debug(session.cmd_output(f'pidof ping'))
    proc_info = session.cmd_output(f'ps -p {pid_ping} -o command').strip()
    LOG.debug(proc_info)
    LOG.debug(session.cmd_output(f'ps -ef|grep ping'))
    session.close()

    ping_cmd = 'ping 127.0.0.1'
    if ping_cmd not in proc_info:
        raise Exception('Cannot find running ping command after save-restore.')
    if upsince_restore != upsince:
        raise Exception(f'Uptime since {upsince_restore} is incorrect,'
                        f'should be {upsince}')
