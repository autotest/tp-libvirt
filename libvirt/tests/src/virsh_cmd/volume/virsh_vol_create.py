import os
import logging
from virttest import virsh, libvirt_storage, libvirt_xml
from virttest.utils_test import libvirt as utlv
from autotest.client.shared import error
from autotest.client import utils
from provider import libvirt_version
from virttest.staging import service


def run(test, params, env):
    """
    Test virsh vol-create command to cover the following matrix:
    pool_type = [dir, fs, netfs]
    volume_format = [raw, bochs, cloop, cow, dmg, iso, qcow, qcow2, qed,
                     vmdk, vpc]

    pool_type = [disk]
    volume_format = [none, linux, fat16, fat32, linux-swap, linux-lvm,
                     linux-raid, extended]

    pool_type = [logical]
    volume_format = [none]

    pool_type = [iscsi, scsi]
    Not supported with format type

    TODO:
    pool_type = [rbd, glusterfs]

    Reference: http://www.libvirt.org/storage.html
    """

    src_pool_type = params.get("src_pool_type")
    src_pool_target = params.get("src_pool_target")
    src_pool_format = params.get("src_pool_format", "")
    pool_vol_num = int(params.get("src_pool_vol_num", '1'))
    src_emulated_image = params.get("src_emulated_image")
    extra_option = params.get("extra_option", "")
    prefix_vol_name = params.get("vol_name", "vol_create_test")
    vol_format = params.get("vol_format", "raw")
    vol_capacity = params.get("vol_capacity", 1048576)
    vol_allocation = params.get("vol_allocation", 1048576)
    image_size = params.get("emulate_image_size", "1G")
    lazy_refcounts = "yes" == params.get("lazy_refcounts")
    status_error = "yes" == params.get("status_error", "no")
    by_xml = "yes" == params.get("create_vol_by_xml", "yes")

    # Stop multipathd to avoid start pool fail(For fs like pool, the new add
    # disk may in use by device-mapper, so start pool will report disk already
    # mounted error).
    multipathd = service.Factory.create_service("multipathd")
    multipathd_status = multipathd.status()
    if multipathd_status:
        multipathd.stop()

    # Set volume xml attribute dictionary, extract all params start with 'vol_'
    # which are for setting volume xml, except 'lazy_refcounts'.
    vol_arg = {}
    for key in params.keys():
        if key.startswith('vol_'):
            if key[4:] in ['capacity', 'allocation', 'owner', 'group']:
                vol_arg[key[4:]] = int(params[key])
            else:
                vol_arg[key[4:]] = params[key]
    vol_arg['lazy_refcounts'] = lazy_refcounts

    pool_type = ['dir', 'disk', 'fs', 'logical', 'netfs', 'iscsi', 'scsi']
    if src_pool_type not in pool_type:
        raise error.TestNAError("pool type %s not in supported type list: %s" %
                                (src_pool_type, pool_type))

    if not libvirt_version.version_compare(1, 0, 0):
        if "--prealloc-metadata" in extra_option:
            raise error.TestNAError("metadata preallocation not supported in"
                                    " current libvirt version.")

    # libvirt acl polkit related params
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    " libvirt version.")

    def post_process_vol(ori_vol_path):
        """
        Create or disactive a volume without libvirt

        :param ori_vol_path: Full path of an original volume
        :retur: Volume name for checking
        """
        process_vol_name = params.get("process_vol_name", "process_vol")
        process_vol_options = params.get("process_vol_options", "")
        process_vol_capacity = params.get("process_vol_capacity", vol_capacity)
        process_vol_cmd = ""
        unsupport_err = "Unsupport do '%s %s' in this test" % (process_vol_by,
                                                               process_vol_type)
        if process_vol_by == "lvcreate":
            process_vol_cmd = "lvcreate -L %s " % process_vol_capacity
            if process_vol_type == "thin":
                if not process_vol_options:
                    process_vol_options = "-T "
                process_vol_cmd += "%s " % process_vol_options
                processthin_pool_name = params.get("processthin_pool_name", "thinpool")
                processthin_vol_name = params.get("processthin_vol_name", "thinvol")
                process_vol_capacity = params.get("process_vol_capacity", "1G")
                os.path.dirname(ori_vol_path)
                process_vol_cmd += "%s/%s " % (os.path.dirname(ori_vol_path),
                                               processthin_pool_name)
                process_vol_cmd += "-V %s " % process_vol_capacity
                process_vol_cmd += "-n %s " % processthin_vol_name
                process_vol_name = processthin_vol_name
            elif process_vol_type == "snapshot":
                if not process_vol_options:
                    process_vol_options = "-s "
                process_vol_cmd += "%s " % process_vol_options
                process_vol_cmd += "-n %s " % process_vol_name
                process_vol_cmd += "%s " % (ori_vol_path)
            else:
                logging.error(unsupport_err)
                return
        elif process_vol_by == "qemu-img" and process_vol_type == "create":
            process_vol_cmd = "qemu-img create "
            process_vol_path = os.path.dirname(ori_vol_path) + "/"
            process_vol_path += process_vol_name
            process_vol_cmd += "%s " % process_vol_options
            process_vol_cmd += "%s " % process_vol_path
            process_vol_cmd += "%s " % process_vol_capacity
        elif process_vol_by == "lvchange" and process_vol_type == "deactivate":
            process_vol_cmd = "lvchange %s " % ori_vol_path
            if not process_vol_options:
                process_vol_options = "-an"
            process_vol_cmd += process_vol_options
        else:
            logging.error(unsupport_err)
            return
        rst = utils.run(process_vol_cmd, ignore_status=True)
        if rst.exit_status:
            if "Snapshots of snapshots are not supported" in rst.stderr:
                logging.debug("%s is already a snapshot volume", ori_vol_path)
                process_vol_name = os.path.basename(ori_vol_path)
            else:
                logging.error(rst.stderr)
                return
        return process_vol_name

    def check_vol(pool_name, vol_name, expect_exist=True):
        """
        Check volume vol_name in pool pool_name
        """
        src_volumes = src_pv.list_volumes().keys()
        logging.debug("Current volumes in %s: %s", pool_name, src_volumes)
        if expect_exist:
            if vol_name not in src_volumes:
                raise error.TestFail("Can't find volume %s in pool %s"
                                     % (vol_name, pool_name))
            # check format in volume xml
            post_xml = volxml.new_from_vol_dumpxml(vol_name, pool_name)
            logging.debug("Volume %s XML: %s" % (vol_name,
                                                 post_xml.xmltreefile))
            if 'format' in post_xml.keys() and vol_format is not None:
                if post_xml.format != vol_format:
                    raise error.TestFail("Volume format %s is not expected"
                                         % vol_format + " as defined.")
        else:
            if vol_name in src_volumes:
                raise error.TestFail("Find volume %s in pool %s, but expect not"
                                     % (vol_name, pool_name))

    fmt_err0 = "Unknown file format '%s'" % vol_format
    fmt_err1 = "Formatting or formatting option not "
    fmt_err1 += "supported for file format '%s'" % vol_format
    fmt_err2 = "Driver '%s' does not support " % vol_format
    fmt_err2 += "image creation"
    fmt_err_list = [fmt_err0, fmt_err1, fmt_err2]
    skip_msg = "Volume format '%s' is not supported by qemu-img" % vol_format
    vol_path_list = []
    try:
        # Create the src pool
        src_pool_name = "virt-%s-pool" % src_pool_type
        pvt = utlv.PoolVolumeTest(test, params)
        pvt.pre_pool(src_pool_name, src_pool_type, src_pool_target,
                     src_emulated_image, image_size=image_size,
                     source_format=src_pool_format)

        src_pv = libvirt_storage.PoolVolume(src_pool_name)
        # Print current pools for debugging
        logging.debug("Current pools:%s",
                      libvirt_storage.StoragePool().list_pools())

        # Create volumes by virsh in a loop
        while pool_vol_num > 0:
            # Set volume xml file
            vol_name = prefix_vol_name + "_%s" % pool_vol_num
            pool_vol_num -= 1
            if by_xml:
                vol_arg['name'] = vol_name
                volxml = libvirt_xml.VolXML()
                newvol = volxml.new_vol(**vol_arg)
                vol_xml = newvol['xml']
                if params.get('setup_libvirt_polkit') == 'yes':
                    utils.run("chmod 666 %s" % vol_xml, ignore_status=True)

                # Run virsh_vol_create to create vol
                logging.debug("Create volume from XML: %s" % newvol.xmltreefile)
                cmd_result = virsh.vol_create(
                    src_pool_name, vol_xml, extra_option,
                    unprivileged_user=unprivileged_user, uri=uri,
                    ignore_status=True, debug=True)
            else:
                # Run virsh_vol_create_as to create_vol
                cmd_result = virsh.vol_create_as(
                    vol_name, src_pool_name, vol_capacity, vol_allocation,
                    vol_format, unprivileged_user=unprivileged_user, uri=uri,
                    ignore_status=True, debug=True)
            # Check result
            try:
                utlv.check_exit_status(cmd_result, status_error)
                check_vol(src_pool_name, vol_name, not status_error)
                if not status_error:
                    vol_path = virsh.vol_path(vol_name,
                                              src_pool_name).stdout.strip()
                    logging.debug("Full path of %s: %s", vol_name, vol_path)
                    vol_path_list.append(vol_path)
            except error.TestFail, e:
                stderr = cmd_result.stderr
                if any(err in stderr for err in fmt_err_list):
                    raise error.TestNAError(skip_msg)
                else:
                    raise e
        # Post process vol by other programs
        process_vol_by = params.get("process_vol_by")
        process_vol_type = params.get("process_vol_type", "")
        expect_vol_exist = "yes" == params.get("expect_vol_exist", "yes")
        if process_vol_by and vol_path_list:
            process_vol = post_process_vol(vol_path_list[0])
            if process_vol is not None:
                try:
                    virsh.pool_refresh(src_pool_name)
                    check_vol(src_pool_name, process_vol, expect_vol_exist)
                except error.TestFail, e:
                    if process_vol_type == "thin":
                        logging.error(e)
                        raise error.TestNAError("You may encounter bug BZ#1060287")
                    else:
                        raise e
            else:
                raise error.TestFail("Post process volume failed")
    finally:
        # Cleanup
        # For old version lvm2(2.02.106 or early), deactivate volume group
        # (destroy libvirt logical pool) will fail if which has deactivated
        # lv snapshot, so before destroy the pool, we need activate it manually
        if src_pool_type == 'logical' and vol_path_list:
            vg_name = vol_path_list[0].split('/')[2]
            utils.run("lvchange -ay %s" % vg_name)
        try:
            pvt.cleanup_pool(src_pool_name, src_pool_type, src_pool_target,
                             src_emulated_image)
        except error.TestFail, detail:
            logging.error(str(detail))
        if multipathd_status:
            multipathd.start()
