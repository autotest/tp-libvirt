import os
import logging

from avocado.utils import process
from avocado.core import exceptions

from virttest import utils_libvirtd
from virttest import libvirt_storage
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml import pool_xml
from virttest.staging import lv_utils

from virttest import libvirt_version


def run(test, params, env):
    """
    Test pool command:virsh pool_autostart

    1) Define a given type pool
    2) Mark pool as autostart
    3) Restart libvirtd and check pool
    4) Destroy the pool
    5) Unmark pool as autostart
    6) Repeate step(3)
    """

    # Initialize the variables
    pool_name = params.get("pool_name", "temp_pool_1")
    pool_type = params.get("pool_type", "dir")
    pool_target = params.get("pool_target", "")
    source_format = params.get("source_format", "")
    source_name = params.get("pool_source_name", "gluster-vol1")
    source_path = params.get("pool_source_path", "/")
    ip_protocal = params.get("ip_protocal", "ipv4")
    pool_ref = params.get("pool_ref", "name")
    pool_uuid = params.get("pool_uuid", "")
    invalid_source_path = params.get("invalid_source_path", "")
    status_error = "yes" == params.get("status_error", "no")
    readonly_mode = "yes" == params.get("readonly_mode", "no")
    pre_def_pool = "yes" == params.get("pre_def_pool", "yes")
    disk_type = params.get("disk_type", "")
    vg_name = params.get("vg_name", "")
    lv_name = params.get("lv_name", "")
    update_policy = params.get("update_policy")

    # Readonly mode
    ro_flag = False
    if readonly_mode:
        logging.debug("Readonly mode test")
        ro_flag = True

    if pool_target is "":
        pool_target = os.path.join(test.tmpdir, pool_target)

    # The file for dumped pool xml
    p_xml = os.path.join(test.tmpdir, "pool.xml.tmp")

    if not libvirt_version.version_compare(1, 0, 0):
        if pool_type == "gluster":
            test.cancel("Gluster pool is not supported in current"
                        " libvirt version.")

    pool_ins = libvirt_storage.StoragePool()
    if pool_ins.pool_exists(pool_name):
        test.fail("Pool %s already exist" % pool_name)

    def check_pool(pool_name, pool_type, checkpoint,
                   expect_value="", expect_error=False):
        """
        Check the pool after autostart it

        :param pool_name: Name of the pool.
        :param pool_type: Type of the pool.
        :param checkpoint: Which part for checking.
        :param expect_value: Expected value.
        :param expect_error: Boolen value, expect command success or fail
        """
        libvirt_pool = libvirt_storage.StoragePool()
        virsh.pool_list(option="--all", debug=True)
        if checkpoint == 'State':
            actual_value = libvirt_pool.pool_state(pool_name)
        if checkpoint == 'Autostart':
            actual_value = libvirt_pool.pool_autostart(pool_name)
        if actual_value != expect_value:
            if not expect_error:
                if checkpoint == 'State' and pool_type in ("dir", "scsi"):
                    debug_msg = "Dir pool should be always active when libvirtd restart. "
                    debug_msg += "See https://bugzilla.redhat.com/show_bug.cgi?id=1238610"
                    logging.debug(debug_msg)
                else:
                    test.fail("Pool %s isn't %s as expected" % (checkpoint, expect_value))
            else:
                logging.debug("Pool %s is %s as expected", checkpoint, actual_value)

    def change_source_path(new_path, update_policy="set"):
        n_poolxml = pool_xml.PoolXML()
        n_poolxml = n_poolxml.new_from_dumpxml(pool_name)
        s_xml = n_poolxml.get_source()
        s_xml.device_path = new_path
        if update_policy == "set":
            n_poolxml.set_source(s_xml)
        elif update_policy == "add":
            n_poolxml.add_source("device", {"path": new_path})
        else:
            test.error("Unsupported policy type")
        logging.debug("After change_source_path:\n%s" %
                      open(n_poolxml.xml).read())
        return n_poolxml

    # Run Testcase
    pvt = utlv.PoolVolumeTest(test, params)
    kwargs = {'image_size': '1G', 'pre_disk_vol': ['100M'],
              'source_name': source_name, 'source_path': source_path,
              'source_format': source_format, 'persistent': True,
              'ip_protocal': ip_protocal, 'emulated_image': "emulated-image",
              'pool_target': pool_target}
    params.update(kwargs)
    pool = pool_name
    clean_mount = False
    new_device = None
    try:
        if pre_def_pool:
            # Step(1)
            # Pool define
            pvt.pre_pool(**params)
            # Remove the partition for disk pool
            # For sometimes the partition will cause pool start failed
            if pool_type == "disk":
                virsh.pool_build(pool_name, "--overwrite", debug=True)
            # Get pool uuid:
            if pool_ref == "uuid" and not pool_uuid:
                pool = pool_ins.get_pool_uuid(pool_name)

            # Setup logical block device
            # Change pool source path
            # Undefine pool
            # Define pool with new xml
            # Start pool
            if update_policy:
                new_device = utlv.setup_or_cleanup_iscsi(True)
                lv_utils.vg_create(vg_name, new_device)
                new_device = utlv.create_local_disk(disk_type, size="0.5",
                                                    vgname=vg_name, lvname=lv_name)
                new_path = new_device
                if invalid_source_path:
                    new_path = invalid_source_path
                if pool_type == "fs":
                    utlv.mkfs(new_device, source_format)
                n_poolxml = change_source_path(new_path, update_policy)
                p_xml = n_poolxml.xml
                if not virsh.pool_undefine(pool_name):
                    test.fail("Undefine pool %s failed" % pool_name)
                if not virsh.pool_define(p_xml):
                    test.fail("Define pool %s from %s failed" % (pool_name, p_xml))
                logging.debug("Start pool %s" % pool_name)
                result = virsh.pool_start(pool_name, ignore_status=True, debug=True)
                utlv.check_exit_status(result, status_error)
                # Mount a valid fs to pool target
                if pool_type == "fs":
                    source_list = []
                    mnt_cmd = ""
                    pool_target = n_poolxml.target_path
                    if invalid_source_path:
                        source_list.append(new_device)
                    else:
                        s_devices = n_poolxml.xmltreefile.findall("//source/device")
                        for dev in s_devices:
                            source_list.append(dev.get('path'))
                    try:
                        for src in source_list:
                            mnt_cmd = "mount %s %s" % (src, pool_target)
                            if not process.system(mnt_cmd, shell=True):
                                clean_mount = True
                    except process.CmdError:
                        test.error("Failed to run %s" % mnt_cmd)

        # Step(2)
        # Pool autostart
        logging.debug("Try to mark pool %s as autostart" % pool_name)
        result = virsh.pool_autostart(pool, readonly=ro_flag,
                                      ignore_status=True, debug=True)
        if not pre_def_pool:
            utlv.check_exit_status(result, status_error)
        if not result.exit_status:
            check_pool(pool_name, pool_type, checkpoint='Autostart',
                       expect_value="yes", expect_error=status_error)

            # Step(3)
            # Restart libvirtd and check pool status
            logging.info("Try to restart libvirtd")
            # Remove the autostart management file
            utils_libvirtd.unmark_storage_autostarted()
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
            check_pool(pool_name, pool_type, checkpoint="State",
                       expect_value="active", expect_error=status_error)

            # Step(4)
            # Pool destroy
            if pool_ins.is_pool_active(pool_name):
                virsh.pool_destroy(pool_name)
                logging.debug("Pool %s destroyed" % pool_name)

            # Step(5)
            # Pool autostart disable
            logging.debug("Try to unmark pool %s as autostart" % pool_name)
            result = virsh.pool_autostart(pool, extra="--disable", debug=True,
                                          ignore_status=True)
            if not pre_def_pool:
                utlv.check_exit_status(result, status_error)
            if not result.exit_status:
                check_pool(pool_name, pool_type, checkpoint='Autostart',
                           expect_value="no", expect_error=status_error)

                # Repeat step (3)
                logging.debug("Try to restart libvirtd")
                libvirtd = utils_libvirtd.Libvirtd()
                libvirtd.restart()
                check_pool(pool_name, pool_type, checkpoint='State',
                           expect_value="inactive", expect_error=status_error)
    finally:
        # Clean up
        logging.debug("Try to clean up env")
        try:
            if clean_mount is True:
                for src in source_list:
                    process.system("umount %s" % pool_target)
            if pre_def_pool:
                pvt.cleanup_pool(**params)
            if new_device:
                utlv.delete_local_disk(disk_type, vgname=vg_name, lvname=lv_name)
                lv_utils.vg_remove(vg_name)
                utlv.setup_or_cleanup_iscsi(False)
            if os.path.exists(p_xml):
                os.remove(p_xml)
        except exceptions.TestFail as details:
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()
            logging.error(str(details))
