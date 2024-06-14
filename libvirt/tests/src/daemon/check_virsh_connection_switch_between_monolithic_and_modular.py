import logging

from virttest.staging import service

from provider.libvirtd import libvirtd_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Verify virsh connection can work well after switching between the legacy
    monolithic daemon and the modular daemon
    """
    try:
        libvirtd_srv = service.Factory.create_service('libvirtd')
        virtqemud_srv = service.Factory.create_service('virtqemud')
        libvirtd_base.check_virsh_connection('before test')
        libvirtd_base.check_service_status('virtqemud', True)
        libvirtd_srv.start()
        libvirtd_base.check_service_status('libvirtd', True)
        libvirtd_base.check_service_status('virtqemud', False)
        libvirtd_base.check_virsh_connection(
            'after switch to legacy monolithic daemon mode')
        virtqemud_srv.start()
        libvirtd_base.check_service_status('libvirtd', False)
        libvirtd_base.check_service_status('virtqemud', True)
        libvirtd_base.check_virsh_connection(
            'after switch back to modular daemon mode')
    finally:
        virtqemud_srv.start()
