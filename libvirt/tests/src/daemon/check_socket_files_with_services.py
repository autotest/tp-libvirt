import logging
import os

from virttest.staging import service

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check the socket files are cleaned or created after stop/start
    daemons' service.
    """
    daemon_list = eval(params.get('service_list'))
    other_socket_files = eval(params.get('other_socket_files'))

    try:
        daemon_srv_list = []
        daemon_list = [f'virt{sub_daemon}d' for sub_daemon in daemon_list]
        for daemon in daemon_list:
            daemon_srv = service.Factory.create_service(daemon)
            daemon_srv_list.append(daemon_srv)
            daemon_srv.start()

        all_sock_files = os.listdir('/var/run/libvirt')

        daemons = daemon_list.copy()
        daemons.remove('virtqemud')
        daemons.remove('virtproxyd')
        daemons.append('libvirt')
        expect_s_files = [[f'{x}-admin-sock',
                           f'{x}-sock',
                           f'{x}-sock-ro'] for x in daemons]
        expect_s_files = [x for y in expect_s_files for x in y]
        expect_s_files.extend(other_socket_files)
        LOG.debug(f'Expect socket files:\n{expect_s_files}')
        diff = set(expect_s_files).difference(set(all_sock_files))
        if diff:
            test.fail(f'Not found expected socket files: {diff}')

        for daemon in daemon_list:
            daemon_sock_srv = service.Factory.create_service(
                daemon + '.socket')
            daemon_sock_srv.stop()

        all_sock_files = os.listdir('/var/run/libvirt')
        LOG.debug(f'All files in /var/run/libvirt\n{all_sock_files}')
        left_s_files = set(expect_s_files).intersection(set(all_sock_files))
        LOG.debug(f'Socket files left: {left_s_files}')
        unexpect = left_s_files.difference(set(other_socket_files))
        if unexpect:
            test.fail(f'Found unexpected socket files left: {unexpect}')

    finally:
        for daemon in daemon_list:
            daemon_sock_srv = service.Factory.create_service(
                daemon + '.socket')
            daemon_sock_srv.start()
