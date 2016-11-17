from autotest.client.shared import utils
from autotest.client.shared import error

from virttest import virsh
from virttest import utils_libvirtd


def run(test, params, env):
    """
    Test the command virsh hostname

    (1) Call virsh hostname
    (2) Call virsh hostname with an unexpected option
    (3) Call virsh hostname with libvirtd service stop
    """

    hostname_result = utils.run("hostname -f", ignore_status=True)
    hostname = hostname_result.stdout.strip()

    # Prepare libvirtd service
    check_libvirtd = params.has_key("libvirtd")
    if check_libvirtd:
        libvirtd = params.get("libvirtd")
        if libvirtd == "off":
            utils_libvirtd.libvirtd_stop()

    # Run test case
    option = params.get("virsh_hostname_options")
    hostname_test = virsh.hostname(option,
                                   ignore_status=True,
                                   debug=True)
    status = 0
    if hostname_test == '':
        status = 1
        hostname_test = None

    # Recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Check status_error
    status_error = params.get("status_error")
    if status_error == "yes":
        if status == 0:
            raise error.TestFail("Command 'virsh hostname %s' succeeded "
                                 "(incorrect command)" % option)
    elif status_error == "no":
        if cmp(hostname, hostname_test) != 0:
            raise error.TestFail(
                "Virsh cmd gives hostname %s != %s." % (hostname_test, hostname))
        if status != 0:
            raise error.TestFail("Command 'virsh hostname %s' failed "
                                 "(correct command)" % option)
