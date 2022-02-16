import logging

from virttest import utils_split_daemons
from virttest.staging import service


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
LOGGER = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Check daemon default status.
    """
    daemon_name = params.get('daemon_name', "")
    socket_name = daemon_name + ".socket"
    daemon_default_enabled = params.get('daemon_default_enabled', "no") == "yes"
    socket_default_enabled = params.get('socket_default_enabled', "no") == "yes"
    require_modular_daemon = params.get('require_modular_daemon', "no") == "yes"

    utils_split_daemons.daemon_mode_check(require_modular_daemon)
    daemon_enabled = service.Factory.create_service(daemon_name).is_enabled()
    LOGGER.debug("%s is%s enabled by default.", daemon_name, ' not' if not daemon_enabled else '')
    socket_enabled = service.Factory.create_service(socket_name).is_enabled()
    LOGGER.debug("%s is%s enabled by default.", socket_name, ' not' if not socket_enabled else '')
    if daemon_enabled != daemon_default_enabled:
        test.fail("The is-enabled of %s is not the same as expected" % daemon_name)
    if socket_enabled != socket_default_enabled:
        test.fail("The is-enabled of %s is not the same as expected" % socket_name)
