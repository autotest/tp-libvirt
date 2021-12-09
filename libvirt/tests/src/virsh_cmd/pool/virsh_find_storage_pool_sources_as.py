import logging

from avocado.utils import process
from avocado.core import exceptions

from virttest import virsh
from virttest import utils_test
from virttest.staging import lv_utils

from virttest import libvirt_version


def run(test, params, env):
    """
    Test command: virsh find-storage-pool-sources-as

    1. Prepare env to provide source storage:
       1). For 'netfs' source type, setup nfs server
       2). For 'iscsi' source type, setup iscsi server
       3). For 'logcial' type pool, setup iscsi storage to create vg
    2. Find the pool source by running virsh cmd
    """

    source_type = params.get("source_type", "")
    source_host = params.get("source_host", "127.0.0.1")
    source_port = params.get("source_port", "")
    source_initiator = params.get("source_initiator", "")
    options = params.get("extra_options", "")
    vg_name = params.get("vg_name", "virttest_vg_0")
    ro_flag = "yes" == params.get("readonly_mode", "no")
    status_error = "yes" == params.get("status_error", "no")

    if not source_type:
        raise exceptions.TestFail("Command requires <type> value")

    if not libvirt_version.version_compare(4, 7, 0):
        if source_type == "iscsi-direct":
            test.cancel("iscsi-drect pool is not supported in current"
                        "libvirt version")

    cleanup_nfs = False
    cleanup_iscsi = False
    cleanup_logical = False

    if source_host == "127.0.0.1":
        if source_type == "netfs":
            # Set up nfs
            res = utils_test.libvirt.setup_or_cleanup_nfs(True)
            selinux_bak = res["selinux_status_bak"]
            cleanup_nfs = True
        if source_type in ["iscsi", "logical", "iscsi-direct"]:
            # Set up iscsi
            try:
                iscsi_device = utils_test.libvirt.setup_or_cleanup_iscsi(True)
                # If we got nothing, force failure
                if not iscsi_device:
                    raise exceptions.TestFail("Did not setup an iscsi device")
                cleanup_iscsi = True
                if source_type == "logical":
                    # Create VG by using iscsi device
                    lv_utils.vg_create(vg_name, iscsi_device)
                    cleanup_logical = True
            except Exception as detail:
                if cleanup_iscsi:
                    utils_test.libvirt.setup_or_cleanup_iscsi(False)
                raise exceptions.TestFail("iscsi setup failed:\n%s" % detail)

    # Run virsh cmd
    options = "%s %s " % (source_host, source_port) + options + " %s" % source_initiator
    if ro_flag:
        logging.debug("Readonly mode test")
    try:
        cmd_result = virsh.find_storage_pool_sources_as(
            source_type,
            options,
            ignore_status=True,
            debug=True,
            readonly=ro_flag)
        utils_test.libvirt.check_exit_status(cmd_result, status_error)
    finally:
        # Clean up
        if cleanup_logical:
            cmd = "pvs |grep %s |awk '{print $1}'" % vg_name
            pv_name = process.run(cmd, shell=True).stdout_text
            lv_utils.vg_remove(vg_name)
            process.run("pvremove %s" % pv_name)
        if cleanup_iscsi:
            utils_test.libvirt.setup_or_cleanup_iscsi(False)
        if cleanup_nfs:
            utils_test.libvirt.setup_or_cleanup_nfs(
                False, restore_selinux=selinux_bak)
