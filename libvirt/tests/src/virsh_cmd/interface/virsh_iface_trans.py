import os
import re
import logging

from avocado.utils import process
from avocado.utils import path as utils_path
from avocado.core import exceptions

from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_package


def netcf_trans_control(test, command="status"):
    """
    Control current network configuration
    :param: command: it may be 'status', 'snapshot-dir', restart,
            change-begin, etc, Note that is the netcf-libs is required
    :returns: return command result
    """
    try:
        # For OS using systemd, this command usually located in /usr/libexec/.
        cmd = utils_path.find_command("netcf-transaction.sh")
    except utils_path.CmdNotFoundError:
        # This is the default location for sysV init.
        old_path = "/etc/rc.d/init.d/netcf-transaction"
        if os.path.isfile(old_path):
            cmd = old_path
        else:
            test.cancel("Cannot find netcf-transaction! "
                        "Make sure you have netcf-libs installed!")
    logging.debug(cmd)

    return process.getoutput("%s %s" % (cmd, command), shell=True)


def write_iface_cfg(iface_cfg, test):
    """
    Create a temporary network configuration file
    :param: iface_cfg: the network configuration file name
    """
    content = "DEVICE='iface-test'\r\nBOOTPROTO='none'\r\nONBOOT='no'"
    try:
        with open(iface_cfg, 'w+') as fp:
            fp.write(content)
    except IOError:
        test.fail("Failed to write %s to %s!" % (content, iface_cfg))


def cleanup(iface_cfg, test, exist_trans="no"):
    """
    Cleanup network transaction if there is already an open transaction
    and cleanup temporary network configuration file if it exists
    :param: iface_cfg: the network configuration file name
    :param: exist_trans: it may be 'yes' or 'no', default is 'no'
    """
    if os.access(iface_cfg, os.R_OK):
        logging.debug("Remove temporary network configuration: %s", iface_cfg)
        os.unlink(iface_cfg)
    if exist_trans == "yes":
        logging.debug("Cleanup an open network transaction")
        netcf_trans_control(test, 'restart')


def iface_trans_begin(params, test):
    """
    Take a snapshot of current network configuration
    :params: the parameter dictionary
    """
    result = virsh.iface_begin()
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")
    netcf_snap_dir = params.get("netcf_snap_dir",
                                "/var/lib/netcf/network-snapshot")

    if status_error == "yes":
        if status:
            logging.info("It's an expected error")
        else:
            test.fail("%d not a expected command"
                      "return value" % status)
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            netcf_status = netcf_trans_control(test)
            logging.debug("%s", netcf_status)

            if not re.search("an open", netcf_status) or \
                    not os.access(netcf_snap_dir, os.R_OK):
                test.fail("Failed to create snapshot for interface")

            logging.info("Succeed to create snapshot of current network")
    else:
        test.fail("The 'status_error' must be 'yes' or 'no': %s"
                  % status_error)


def iface_trans_commit(params, test):
    """
    Commit the new network configuration
    :params: the parameter dictionary
    """
    result = virsh.iface_commit()
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")
    iface_cfg = params.get("iface_cfg")

    if status_error == "yes":
        if status:
            logging.info("It's an expected error")
        else:
            test.fail("%d not a expected command"
                      "return value" % status)
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            netcf_status = netcf_trans_control(test)
            logging.debug("%s", netcf_status)

            if not re.search("No open", netcf_status) or \
                    not os.access(iface_cfg, os.R_OK):
                test.fail("Failed to commit snapshot")

            logging.info("Succeed to commit snapshot of current network")
    else:
        test.fail("The 'status_error' must be 'yes' or 'no': %s"
                  % status_error)


def iface_trans_rollback(params, test):
    """
    Rollback network configuration to last snapshot if one exist
    :params: the parameter dictionary
    """
    result = virsh.iface_rollback()
    status = result.exit_status

    # Check status_error
    status_error = params.get("status_error", "no")
    iface_cfg = params.get("iface_cfg")
    netcf_snap_dir = params.get("netcf_snap_dir",
                                "/var/lib/netcf/network-snapshot")

    if status_error == "yes":
        if status:
            logging.info("It's an expected error")
        else:
            test.fail("%d not a expected command"
                      "return value" % status)
    elif status_error == "no":
        if status:
            test.fail(result.stderr)
        else:
            netcf_status = netcf_trans_control(test)
            logging.debug("%s", netcf_status)

            if not re.search("No open", netcf_status):
                test.fail("Failed to rollback network to "
                          "last snapshot")

            if os.access(netcf_snap_dir, os.R_OK):
                test.fail("%s exists" % netcf_snap_dir)

            if os.access(iface_cfg, os.R_OK):
                test.fail("%s exists" % iface_cfg)

            logging.info("Succeed to rollback network to last snapshot")
    else:
        test.fail("The 'status_error' must be 'yes' or 'no': %s"
                  % status_error)


def run(test, params, env):
    """
    Test the network transaction and cover virsh iface-{begin,commit,rollback}

    1. Positive testing
       1.1 begin or/and commit testing with libvirtd running
       1.2 begin or/and rollback testing with libvirtd running
    2. Negative testing
       2.1 no pending transaction testing
       2.2 there is already an open transaction testing
       2.3 break network transaction testing
           2.3.1 begin and commit testing with libvirtd restart
           2.3.2 begin and rollback testing with libvirtd restart
    """

    if not utils_package.package_install(["mlocate"]):
        test.cancel("Failed to install dependency package mlocate"
                    " on host")

    if process.system("updatedb", shell=True):
        test.cancel("update a database for mlocate failed")

    # Run test case
    status_error = params.get("status_error", "no")
    libvirtd = params.get("libvirtd", "on")
    exist_trans = params.get("exist_trans", "no")
    iface_name = params.get("iface_name", "ifcfg-test")
    transaction = params.get("iface_transaction", "")

    network_script_dir = "/etc/sysconfig/network-scripts"

    iface_cfg = os.path.join(network_script_dir, iface_name)
    netcf_snap_dir = netcf_trans_control(test, "snapshot-dir")

    params['iface_cfg'] = iface_cfg
    params['netcf_snap_dir'] = netcf_snap_dir

    # positive and negative testing #########

    try:
        if status_error == "no":
            # Do begin-commit testing
            if transaction == "begin_commit":
                iface_trans_begin(params, test)
                write_iface_cfg(iface_cfg, test)
                # Break begin-commit operation
                if libvirtd == "restart":
                    utils_libvirtd.service_libvirtd_control("restart")
                try:
                    iface_trans_commit(params, test)
                except exceptions.TestError:
                    cleanup(iface_cfg, test, exist_trans)

                # Only cleanup temporary network configuration file
                cleanup(iface_cfg, test)

            # Do begin-rollback testing
            elif transaction == "begin_rollback":
                iface_trans_begin(params, test)
                write_iface_cfg(iface_cfg, test)
                # Break begin-rollback operation
                if libvirtd == "restart":
                    utils_libvirtd.service_libvirtd_control("restart")
                try:
                    iface_trans_rollback(params, test)
                except exceptions.TestError:
                    cleanup(iface_cfg, test, exist_trans)
            else:
                test.fail("The 'transaction' must be 'begin_commit' or"
                          " 'begin_rollback': %s" % status_error)

        if status_error == "yes":
            # No pending transaction
            if exist_trans != "yes":
                iface_trans_commit(params, test)
                iface_trans_rollback(params, test)
            # There is already an open transaction
            else:
                netcf_trans_control(test, 'change-begin')
                iface_trans_begin(params, test)
    finally:
        # Cleanup network transaction and temporary configuration file
        cleanup(iface_cfg, test, exist_trans)
