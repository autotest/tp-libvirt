import os

from virttest import libvirt_version
from virttest import virt_admin
from virttest import utils_libvirtd
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test parameters in processing controls of libvirt daemons

    1) Set max_clients and max_anonymous_clients to new values;
    3) Restart libvirt daemons
    4) check whether daemon can be started successfully
    """

    libvirt_version.is_libvirt_feature_supported(params)
    nclients_max = params.get("nclients_maxi")
    nclients_unauth_max = params.get("nclients_unauth_maxi")
    status_error = params.get("status_error") == "yes"
    server_name = params.get("server_name")
    expected_error = params.get("expected_error")

    if not server_name:
        server_name = virt_admin.check_server_name()

    config = virt_admin.managed_daemon_config()
    daemon = utils_libvirtd.Libvirtd("virtproxyd")

    try:

        config.max_clients = nclients_max
        config.max_anonymous_clients = nclients_unauth_max
        ret = daemon.restart()

        if status_error == ret:
            test.fail("Should be %s to restart the daemon" % ("failed" if ret else "successful"))

        if status_error:
            libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
            libvirt.check_logfile(expected_error, libvirtd_log_file, str_in_log=True)

    finally:
        config.restore()
        daemon.restart()
