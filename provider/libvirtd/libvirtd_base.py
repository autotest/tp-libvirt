
import logging

from avocado.core import exceptions
from virttest import virsh
from virttest.staging import service

LOG = logging.getLogger('avocado.' + __name__)


def check_service_status(service_name, expect_active=None,
                         expect_enabled=None):
    """
    Check status of service

    :param service_name: name of service to be checked
    :param expect_active: expect service status, True if active,
                          False if inactive, defaults to None
    :param expect_enabled: expect service enablement, True if enabled
                           False if disenabled, defaults to None
    """
    LOG.info(f'Check service status of {service_name}')
    srvc = service.Factory.create_service(service_name)
    LOG.debug(f'Service [{service_name}] status is '
              f'{"active" if srvc.status() else "inactive"}.\n'
              f'Service [{service_name}] is '
              f'{"enabled" if srvc.is_enabled() else "disabled"}')
    if expect_active is not None:
        if srvc.status() == expect_active:
            LOG.info(f'Service status check PASSED')
        else:
            raise exceptions.TestFail(f'Expect service [{service_name}] to be '
                                      f'{"active" if expect_active else "inactive"}')
    if expect_enabled is not None:
        if srvc.is_enabled() is expect_enabled:
            LOG.info(f'Service enable check PASSED')
        else:
            raise exceptions.TestFail(f'Expect service [{service_name}] to be '
                                      f'{"enabled" if expect_enabled else "disabled"}')


def check_virsh_connection(msg):
    """
    Check virsh connection by running virsh list

    :param msg: message to display
    """
    LOG.info(f'Check virsh connection {msg}')
    conn_result = virsh.dom_list(debug=True)
    if conn_result.exit_status == 0:
        LOG.info(f'Virsh connection check {msg} PASSED')
    else:
        raise exceptions.TestFail(f'Virsh connection {msg} FAILED:\n'
                                  f'{conn_result.stderr_text}')
