import logging
import time

from avocado.utils import process

from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_split_daemons

from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test libvirt should disallow negative value for options needed positive
    integer etc in libvirtd.conf.

    """
    check_string_in_log = params.get("check_string_in_log")
    daemon_name = params.get("daemon_name")
    parameter_name = params.get("parameter_name")
    parameter_value = params.get("parameter_value")
    log_file = params.get("log_file")
    require_modular_daemon = params.get('require_modular_daemon', "no") == "yes"
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    utils_split_daemons.daemon_mode_check(require_modular_daemon)
    daemon = utils_libvirtd.Libvirtd(daemon_name)
    daemon_conf = utils_config.get_conf_obj(daemon_name)

    try:
        cmd = "echo ' ' > %s" % log_file
        process.run(cmd, shell=True)

        daemon_conf[parameter_name] = parameter_value
        if not daemon.restart(wait_for_start=False):
            LOG.info("%s restart failed as expected.", daemon_name)
        else:
            test.fail("Expect %s restart failed, but successfully." % daemon_name)

        time.sleep(1)
        libvirt.check_logfile(check_string_in_log, log_file, str_in_log=True)

    finally:
        daemon_conf.restore()
        daemon.restart()
