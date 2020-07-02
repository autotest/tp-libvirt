import logging

from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_libvirtd

from virttest import libvirt_version


def run(test, params, env):
    """
    Test the command virsh version

    (1) Call virsh version
    (2) Call virsh version with an unexpected option
    (3) Call virsh version with libvirtd service stop
    """

    connect_uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                              "default"))
    libvirtd = params.get("libvirtd", "on")
    option = params.get("virsh_version_options")
    status_error = (params.get("status_error") == "yes")

    # Prepare libvirtd service
    if libvirtd == "off":
        utils_libvirtd.libvirtd_stop()

    # Run test case
    result = virsh.version(option, uri=connect_uri, debug=True)

    # Recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Check status_error
    if status_error:
        if not result.exit_status:
            if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                             "and command should succeed.")
            else:
                test.fail("Command 'virsh version %s' succeeded "
                          "(incorrect command)" % option)
    else:
        if result.exit_status:
            test.fail("Command 'virsh version %s' failed "
                      "(correct command)" % option)
        if option.count("daemon") and not result.stdout.count("daemon"):
            test.fail("No daemon information outputed!")
