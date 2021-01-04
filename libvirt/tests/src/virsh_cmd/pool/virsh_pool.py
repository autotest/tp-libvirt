import re
import os
import logging

from avocado.core import exceptions

from virttest import utils_libvirtd
from virttest import data_dir
from virttest import libvirt_storage
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from virttest.staging import service
from virttest.libvirt_xml import pool_xml
from virttest import libvirt_version
from virttest import element_tree as ET
from virttest import data_dir


def run(test, params, env):
    """
    Test the virsh pool commands

    (1) Define a given type pool
    (2) List pool with '--inactive --type' options
    (3) Dumpxml for the pool
    (4) Undefine the pool
    (5) Define pool by using the XML file in step (3)
    (6) Build the pool(except 'disk' type pool
        For 'fs' type pool, cover --overwrite and --no-overwrite options
    (7) Start the pool
    (8) List pool with '--persistent --type' options
    (9) Mark pool autostart
    (10) List pool with '--autostart --type' options
    (11) Restart libvirtd and list pool with '--autostart --persistent' options
    (12) Destroy the pool
    (13) Unmark pool autostart
    (14) Repeat step (11)
    (15) Start the pool
    (16) Get pool info
    (17) Get pool uuid by name
    (18) Get pool name by uuid
    (19) Refresh the pool
         For 'dir' type pool, touch a file under target path and refresh again
         to make the new file show in vol-list.
    (20) Check pool 'Capacity', 'Allocation' and 'Available'
         Create a over size vol in pool(expect fail), then check these values
    (21) Undefine the pool, and this should fail as pool is still active
    (22) Destroy the pool
    (23) Delete pool for 'dir' type pool. After the command, the pool object
         will still exist but target path will be deleted
    (24) Undefine the pool
    """

    # Initialize the variables
    pool_name = params.get("pool_name", "temp_pool_1")
    pool_type = params.get("pool_type", "dir")
    pool_target = params.get("pool_target", "")
    source_format = params.get("source_format", "")
    source_name = params.get("pool_source_name", "gluster-vol1")
    source_path = params.get("pool_source_path", "/")
    new_pool_name = params.get("new_pool_name", "")
    build_option = params.get("build_option", "")
    source_initiator = params.get("source_initiator", "")
    same_source_test = "yes" == params.get("same_source_test", "no")
    customize_initiator_iqn = "yes" == params.get("customize_initiator_iqn", "no")
    # The file for dumped pool xml
    poolxml = os.path.join(data_dir.get_tmp_dir(), "pool.xml.tmp")
    if os.path.dirname(pool_target) is "":
        pool_target = os.path.join(data_dir.get_tmp_dir(), pool_target)
    vol_name = params.get("volume_name", "temp_vol_1")
    # Use pool name as VG name
    status_error = "yes" == params.get("status_error", "no")
    vol_path = os.path.join(pool_target, vol_name)
    ip_protocal = params.get('ip_protocal', 'ipv4')
    source_protocol_ver = params.get('source_protocol_ver', "no")

    if not libvirt_version.version_compare(1, 0, 0):
        if pool_type == "gluster":
            test.cancel("Gluster pool is not supported in current"
                        " libvirt version.")
    if not libvirt_version.version_compare(4, 7, 0):
        if pool_type == "iscsi-direct":
            test.cancel("iSCSI-direct pool is not supported in current"
                        "libvirt version.")
    if source_initiator and not libvirt_version.version_compare(6, 10, 0):
        test.cancel("Source_initiator option is not supported in current"
                    " libvirt_version.")
    if source_protocol_ver == "yes" and not libvirt_version.version_compare(4, 5, 0):
        test.cancel("source-protocol-ver is not supported on current version.")

    def check_pool_list(pool_name, option="--all", expect_error=False):
        """
        Check pool by running pool-list command with given option.

        :param pool_name: Name of the pool
        :param option: option for pool-list command
        :param expect_error: Boolean value, expect command success or fail
        """
        found = False
        # Get the list stored in a variable
        result = virsh.pool_list(option, ignore_status=True)
        utlv.check_exit_status(result, False)
        output = re.findall(r"(\S+)\ +(\S+)\ +(\S+)",
                            str(result.stdout.strip()))
        for item in output:
            if pool_name in item[0]:
                found = True
                break
        if found:
            logging.debug("Find pool '%s' in pool list.", pool_name)
        else:
            logging.debug("Not find pool %s in pool list.", pool_name)
        if expect_error and found:
            test.fail("Unexpect pool '%s' exist." % pool_name)
        if not expect_error and not found:
            test.fail("Expect pool '%s' doesn't exist." % pool_name)

    def check_vol_list(vol_name, pool_name):
        """
        Check volume from the list

        :param vol_name: Name of the volume
        :param pool_name: Name of the pool
        """
        found = False
        # Get the volume list stored in a variable
        result = virsh.vol_list(pool_name, ignore_status=True)
        utlv.check_exit_status(result)

        output = re.findall(r"(\S+)\ +(\S+)", str(result.stdout.strip()))
        for item in output:
            if vol_name in item[0]:
                found = True
                break
        if found:
            logging.debug(
                "Find volume '%s' in pool '%s'.", vol_name, pool_name)
        else:
            test.fail(
                "Not find volume '%s' in pool '%s'." %
                (vol_name, pool_name))

    def is_in_range(actual, expected, error_percent):
        deviation = 100 - (100 * (float(actual) / float(expected)))
        logging.debug("Deviation: %0.2f%%", float(deviation))
        return float(deviation) <= float(error_percent)

    def check_pool_info(pool_info, check_point, value):
        """
        Check the pool name, uuid, etc.

        :param pool_info: A dict include pool's information
        :param key: Key of pool info dict, available value: Name, UUID, State
                    Persistent, Autostart, Capacity, Allocation, Available
        :param value: Expect value of pool_info[key]
        """
        if pool_info is None:
            test.fail("Pool info dictionary is needed.")
        val_tup = ('Capacity', 'Allocation', 'Available')
        if check_point in val_tup and float(value.split()[0]):
            # As from bytes to GiB, could cause deviation, and it should not
            # exceed 1 percent.
            if is_in_range(float(pool_info[check_point].split()[0]),
                           float(value.split()[0]), 1):
                logging.debug("Pool '%s' is '%s'.", check_point, value)
            else:
                test.fail("Pool '%s' isn't '%s'." %
                          (check_point, value))
        else:
            if pool_info[check_point] == value:
                logging.debug("Pool '%s' is '%s'.", check_point, value)
            else:
                test.fail("Pool '%s' isn't '%s'." %
                          (check_point, value))

    # Stop multipathd to avoid start pool fail(For fs like pool, the new add
    # disk may in use by device-mapper, so start pool will report disk already
    # mounted error).
    multipathd = service.Factory.create_service("multipathd")
    multipathd_status = multipathd.status()
    if multipathd_status:
        multipathd.stop()

    # Run Testcase
    pvt = utlv.PoolVolumeTest(test, params)
    kwargs = {'image_size': '1G', 'pre_disk_vol': ['100M'],
              'source_name': source_name, 'source_path': source_path,
              'source_format': source_format, 'persistent': True,
              'ip_protocal': ip_protocal, 'emulated_image': "emulated-image",
              'pool_target': pool_target, 'source_initiator': source_initiator,
              'source_protocol_ver': source_protocol_ver}
    params.update(kwargs)

    try:
        _pool = libvirt_storage.StoragePool()
        # Step (1)
        # Pool define
        pvt.pre_pool(**params)

        # Step (2)
        # Pool list
        option = "--inactive --type %s" % pool_type
        check_pool_list(pool_name, option)

        # Step (3)
        # Pool dumpxml
        xml = virsh.pool_dumpxml(pool_name, to_file=poolxml)
        logging.debug("Pool '%s' XML:\n%s", pool_name, xml)

        # Update pool name
        if new_pool_name:
            if "/" in new_pool_name:
                new_pool_name = new_pool_name.replace("/", "\/")
                logging.debug(new_pool_name)
            p_xml = pool_xml.PoolXML.new_from_dumpxml(pool_name)
            p_xml.name = new_pool_name
            del p_xml.uuid
            poolxml = p_xml.xml
            logging.debug("XML after update pool name:\n%s" % p_xml)

        # Update host name
        if same_source_test:
            s_xml = p_xml.get_source()
            s_xml.host_name = "192.168.1.1"
            p_xml.set_source(s_xml)
            poolxml = p_xml.xml
            logging.debug("XML after update host name:\n%s" % p_xml)

        if customize_initiator_iqn:
            initiator_iqn = params.get("initiator_iqn",
                                       "iqn.2018-07.com.virttest:pool.target")
            p_xml = pool_xml.PoolXML.new_from_dumpxml(pool_name)
            s_node = p_xml.xmltreefile.find('/source')
            i_node = ET.SubElement(s_node, 'initiator')
            ET.SubElement(i_node, 'iqn', {'name': initiator_iqn})
            p_xml.xmltreefile.write()
            poolxml = p_xml.xml
            logging.debug('XML after add Multi-IQN:\n%s' % p_xml)

        # Step (4)
        # Undefine pool
        if not same_source_test:
            result = virsh.pool_undefine(pool_name)
            utlv.check_exit_status(result)
            check_pool_list(pool_name, "--all", True)

        # Step (5)
        # Define pool from XML file
        result = virsh.pool_define(poolxml, debug=True)
        # Give error msg when exit status is not expected
        if "/" in new_pool_name and not result.exit_status:
            error_msg = "https://bugzilla.redhat.com/show_bug.cgi?id=639923 "
            error_msg += "is helpful for tracing this bug."
            logging.error(error_msg)
        if "." in new_pool_name and result.exit_status:
            error_msg = "https://bugzilla.redhat.com/show_bug.cgi?id=1333248 "
            error_msg += "is helpful for tracing this bug."
            logging.error(error_msg)
        if same_source_test and not result.exit_status:
            error_msg = "https://bugzilla.redhat.com/show_bug.cgi?id=1171984 "
            error_msg += "is helpful for tracing this bug."
            logging.error(error_msg)
        utlv.check_exit_status(result, status_error)
        if not result.exit_status:
            # Step (6)
            # Buid pool
            # '--overwrite/--no-overwrite' just for fs/disk/logiacl type pool
            # disk/fs pool: as prepare step already make label and create filesystem
            #               for the disk, use '--overwrite' is necessary
            # logical_pool: build pool will fail if VG already exist, BZ#1373711
            if new_pool_name:
                pool_name = new_pool_name
            if pool_type != "logical":
                result = virsh.pool_build(pool_name, build_option, ignore_status=True)
                utlv.check_exit_status(result)

            # Step (7)
            # Pool start
            result = virsh.pool_start(pool_name, debug=True, ignore_status=True)
            utlv.check_exit_status(result)

            # Step (8)
            # Pool list
            option = "--persistent --type %s" % pool_type
            check_pool_list(pool_name, option)

            # Step (9)
            # Pool autostart
            result = virsh.pool_autostart(pool_name, ignore_status=True)
            utlv.check_exit_status(result)

            # Step (10)
            # Pool list
            option = "--autostart --type %s" % pool_type
            check_pool_list(pool_name, option)

            # Step (11)
            # Restart libvirtd and check the autostart pool
            utils_libvirtd.unmark_storage_autostarted()
            utils_libvirtd.libvirtd_restart()
            option = "--autostart --persistent"
            check_pool_list(pool_name, option)

            # Step (12)
            # Pool destroy
            if virsh.pool_destroy(pool_name):
                logging.debug("Pool %s destroyed.", pool_name)
            else:
                test.fail("Destroy pool % failed." % pool_name)

            # Step (13)
            # Pool autostart disable
            result = virsh.pool_autostart(pool_name, "--disable",
                                          ignore_status=True)
            utlv.check_exit_status(result)

            # Step (14)
            # Repeat step (11)
            utils_libvirtd.libvirtd_restart()
            option = "--autostart"
            check_pool_list(pool_name, option, True)

            # Step (15)
            # Pool start
            # When libvirtd starts up, it'll check to see if any of the storage
            # pools have been activated externally. If so, then it'll mark the
            # pool as active. This is independent of autostart.
            # So a directory based storage pool is thus pretty much always active,
            # and so as the SCSI pool.
            if pool_type not in ["dir", 'scsi']:
                result = virsh.pool_start(pool_name, ignore_status=True)
                utlv.check_exit_status(result)

            # Step (16)
            # Pool info
            pool_info = _pool.pool_info(pool_name)
            logging.debug("Pool '%s' info:\n%s", pool_name, pool_info)

            # Step (17)
            # Pool UUID
            result = virsh.pool_uuid(pool_info["Name"], ignore_status=True)
            utlv.check_exit_status(result)
            check_pool_info(pool_info, "UUID", result.stdout.strip())

            # Step (18)
            # Pool Name
            result = virsh.pool_name(pool_info["UUID"], ignore_status=True)
            utlv.check_exit_status(result)
            check_pool_info(pool_info, "Name", result.stdout.strip())

            # Step (19)
            # Pool refresh for 'dir' type pool
            if pool_type == "dir":
                os.mknod(vol_path)
                result = virsh.pool_refresh(pool_name)
                utlv.check_exit_status(result)
                check_vol_list(vol_name, pool_name)

            # Step (20)
            # Create an over size vol in pool(expect fail), then check pool:
            # 'Capacity', 'Allocation' and 'Available'
            # For NFS type pool, there's a bug(BZ#1077068) about allocate volume,
            # and glusterfs pool not support create volume, so not test them
            if pool_type != "netfs":
                vol_capacity = "10000G"
                vol_allocation = "10000G"
                result = virsh.vol_create_as("oversize_vol", pool_name,
                                             vol_capacity, vol_allocation, "raw")
                utlv.check_exit_status(result, True)
                new_info = _pool.pool_info(pool_name)
                check_items = ["Capacity", "Allocation", "Available"]
                for i in check_items:
                    check_pool_info(pool_info, i, new_info[i])

            # Step (21)
            # Undefine pool, this should fail as the pool is active
            result = virsh.pool_undefine(pool_name, ignore_status=True)
            utlv.check_exit_status(result, expect_error=True)
            check_pool_list(pool_name, "", False)

            # Step (22)
            # Pool destroy
            if virsh.pool_destroy(pool_name):
                logging.debug("Pool %s destroyed.", pool_name)
            else:
                test.fail("Destroy pool % failed." % pool_name)

            # Step (23)
            # Pool delete for 'dir' type pool
            if pool_type == "dir":
                for f in os.listdir(pool_target):
                    os.remove(os.path.join(pool_target, f))
                    result = virsh.pool_delete(pool_name, ignore_status=True)
                    utlv.check_exit_status(result)
                    option = "--inactive --type %s" % pool_type
                    check_pool_list(pool_name, option)
                    if os.path.exists(pool_target):
                        test.fail("The target path '%s' still exist." %
                                  pool_target)
                        result = virsh.pool_start(pool_name, ignore_status=True)
                        utlv.check_exit_status(result, True)

            # Step (24)
            # Pool undefine
                result = virsh.pool_undefine(pool_name, ignore_status=True)
                utlv.check_exit_status(result)
                check_pool_list(pool_name, "--all", True)
    finally:
        # Clean up
        try:
            pvt.cleanup_pool(**params)
            utlv.setup_or_cleanup_iscsi(False)
        except exceptions.TestFail as detail:
            logging.error(str(detail))
        if multipathd_status:
            multipathd.start()
        if os.path.exists(poolxml):
            os.remove(poolxml)
