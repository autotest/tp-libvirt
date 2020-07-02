import logging

from avocado.utils import process
from avocado.core import exceptions

from virttest import xml_utils
from virttest import utils_test
from virttest import virsh
from virttest.staging import lv_utils

from virttest import libvirt_version


def run(test, params, env):
    """
    Test command: virsh find-storage-pool-sources

    1. Prepare env to provide source storage if use localhost:
       1). For 'netfs' source type, setup nfs server
       2). For 'iscsi' source type, setup iscsi server
       3). For 'logical' type pool, setup iscsi storage to create vg
       4). Prepare srcSpec xml file if not given
    2. Find the pool sources by running virsh cmd
    """

    source_type = params.get("source_type", "")
    source_host = params.get("source_host", "127.0.0.1")
    source_initiator = params.get("source_initiator", "")
    srcSpec = params.get("source_Spec", "")
    vg_name = params.get("vg_name", "virttest_vg_0")
    ro_flag = "yes" == params.get("readonly_mode", "no")
    status_error = "yes" == params.get("status_error", "no")
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise exceptions.TestSkipError("API acl test not supported in "
                                           "current libvirt version.")

    if not libvirt_version.version_compare(4, 7, 0):
        if source_type == "iscsi-direct":
            test.cancel("iscsi-drect pool is not supported in current"
                        "libvirt version")

    if not source_type:
        raise exceptions.TestFail("Command requires <type> value")

    cleanup_nfs = False
    cleanup_iscsi = False
    cleanup_logical = False

    # Prepare source storage
    if source_host == "127.0.0.1":
        if source_type == "netfs":
            # Set up nfs
            res = utils_test.libvirt.setup_or_cleanup_nfs(True)
            selinux_bak = res["selinux_status_bak"]
            cleanup_nfs = True
        if source_type in ["iscsi", "logical", "iscsi-direct"]:
            # Set up iscsi
            iscsi_device = utils_test.libvirt.setup_or_cleanup_iscsi(True)
            # If we got nothing, force failure
            if not iscsi_device:
                raise exceptions.TestFail("Did not setup an iscsi device")
            cleanup_iscsi = True
            if source_type == "logical":
                # Create vg by using iscsi device
                try:
                    lv_utils.vg_create(vg_name, iscsi_device)
                except Exception as detail:
                    utils_test.libvirt.setup_or_cleanup_iscsi(False)
                    raise exceptions.TestFail("vg_create failed: %s" % detail)
                cleanup_logical = True

    # Prepare srcSpec xml
    if srcSpec:
        if srcSpec == "INVALID.XML":
            src_xml = "<invalid><host name='#@!'/><?source>"
        elif srcSpec == "VALID.XML":
            if source_type == "iscsi-direct":
                src_xml = "<source><host name='%s'/><initiator><iqn name='%s'/></initiator></source>" % (source_host, source_initiator)
            else:
                src_xml = "<source><host name='%s'/></source>" % source_host
        srcSpec = xml_utils.TempXMLFile().name
        with open(srcSpec, "w+") as srcSpec_file:
            srcSpec_file.write(src_xml)
            logging.debug("srcSpec file content:\n%s", srcSpec_file.read())

    if params.get('setup_libvirt_polkit') == 'yes' and srcSpec:
        cmd = "chmod 666 %s" % srcSpec
        process.run(cmd)

    if ro_flag:
        logging.debug("Readonly mode test")

    # Run virsh cmd
    try:
        cmd_result = virsh.find_storage_pool_sources(
            source_type,
            srcSpec,
            ignore_status=True,
            debug=True,
            unprivileged_user=unprivileged_user,
            uri=uri,
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
