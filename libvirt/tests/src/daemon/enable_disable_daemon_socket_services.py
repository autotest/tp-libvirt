import logging

from virttest.staging import service

from provider.libvirtd import libvirtd_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Verify socket services will be in expected status after enable/disable
    """
    service_list = eval(params.get('service_list'))
    sock_type = params.get('sock_type') + '.socket'
    daemon_list = [f'virt{ser}d' for ser in service_list]

    # Disable main socket before test
    for daemon in daemon_list:
        socket_srv = service.Factory.create_service(daemon + '.socket')
        socket_srv.disable()

    try:
        LOG.info('Test enable socket services')
        for daemon in daemon_list:
            socket_srv = service.Factory.create_service(daemon + sock_type)
            LOG.debug(f'Enable service [{socket_srv}]')
            socket_srv.enable()
            libvirtd_base.check_service_status(daemon + '.socket',
                                               expect_enabled=True)
            libvirtd_base.check_service_status(daemon + '-ro.socket',
                                               expect_enabled=True)
            libvirtd_base.check_service_status(daemon + '-admin.socket',
                                               expect_enabled=True)

        LOG.info('Test disable socket services')
        for daemon in daemon_list:
            socket_srv = service.Factory.create_service(daemon + sock_type)
            LOG.debug(f'Disable service [{socket_srv}]')
            socket_srv.disable()
            libvirtd_base.check_service_status(daemon + '.socket',
                                               expect_enabled=False)
            libvirtd_base.check_service_status(daemon + '-ro.socket',
                                               expect_enabled=False)
            libvirtd_base.check_service_status(daemon + '-admin.socket',
                                               expect_enabled=False)
    finally:
        for daemon in daemon_list:
            socket_srv = service.Factory.create_service(daemon + '.socket')
            socket_srv.enable()
