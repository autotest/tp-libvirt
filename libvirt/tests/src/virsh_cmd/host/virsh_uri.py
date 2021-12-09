import logging

from avocado.utils import process

from virttest import libvirt_vm
from virttest import virsh
from virttest import utils_libvirtd
from virttest import ssh_key

from virttest import libvirt_version


def run(test, params, env):
    """
    Test the command virsh uri

    (1) Call virsh uri
    (2) Call virsh -c remote_uri uri
    (3) Call virsh uri with an unexpected option
    (4) Call virsh uri with libvirtd service stop
    """

    connect_uri = libvirt_vm.normalize_connect_uri(params.get("connect_uri",
                                                              "default"))

    option = params.get("virsh_uri_options")
    unprivileged_user = params.get('unprivileged_user')

    remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
    remote_pwd = params.get("remote_pwd", None)
    remote_user = params.get("remote_user", "root")

    # Forming the uri using the api
    target_uri = params.get("target_uri")
    remote_ref = params.get("uri_remote_ref", "")

    if remote_ref:
        if target_uri.count('EXAMPLE.COM'):
            test.cancel(
                'target_uri configuration set to sample value')
        logging.info("The target_uri: %s", target_uri)
        cmd = "virsh -c %s uri" % target_uri
    else:
        cmd = "virsh uri %s" % option

    # Prepare libvirtd service
    check_libvirtd = "libvirtd" in list(params.keys())
    if check_libvirtd:
        libvirtd = params.get("libvirtd")
        if libvirtd == "off":
            utils_libvirtd.libvirtd_stop()

    # Run test case
    logging.info("The command: %s", cmd)

    # setup autologin for ssh to remote machine to execute commands
    if remote_ref:
        ssh_key.setup_ssh_key(remote_ip, remote_user, remote_pwd)

    if unprivileged_user:
        if process.run("id %s" % unprivileged_user, ignore_status=True).exit_status != 0:
            process.run("useradd %s" % unprivileged_user)
    try:
        if remote_ref == "remote" or unprivileged_user:
            connect_uri = target_uri
        uri_test = virsh.canonical_uri(option,
                                       unprivileged_user=unprivileged_user,
                                       uri=connect_uri,
                                       ignore_status=False,
                                       debug=True)
        status = 0  # good
    except process.CmdError:
        status = 1  # bad
        uri_test = ''
        if unprivileged_user:
            process.run("userdel %s" % unprivileged_user)

    # Recover libvirtd service start
    if libvirtd == "off":
        utils_libvirtd.libvirtd_start()

    # Check status_error
    status_error = params.get("status_error", "no")
    if status_error == "yes":
        if status == 0:
            if libvirtd == "off" and libvirt_version.version_compare(5, 6, 0):
                logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                             "and command should succeed.")
            else:
                test.fail("Command: %s  succeeded "
                          "(incorrect command)" % cmd)
        else:
            logging.info("command: %s is a expected error", cmd)
    elif status_error == "no":
        if target_uri != uri_test:
            test.fail("Virsh cmd uri %s != %s." %
                      (uri_test, target_uri))
        if status != 0:
            test.fail("Command: %s  failed "
                      "(correct command)" % cmd)
