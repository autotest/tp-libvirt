import os
import logging
import re
import base64
import locale

from avocado.core import exceptions
from avocado.utils import process
from avocado.core import exceptions

from virttest import virsh
from virttest import libvirt_storage
from virttest import libvirt_xml
from virttest import test_setup
from virttest import virt_vm
from virttest.utils_test import libvirt as utlv
from virttest.staging import service
from virttest.libvirt_xml import secret_xml
from virttest.libvirt_xml import vm_xml

from virttest import libvirt_version


def run(test, params, env):
    """
    Test virsh vol-create and vol-create-as command to cover the following matrix:
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
    incomplete_target = "yes" == params.get("incomplete_target", "no")
    luks_encrypted = "luks" == params.get("encryption_method")
    encryption_secret_type = params.get("encryption_secret_type", "passphrase")
    virsh_readonly_mode = 'yes' == params.get("virsh_readonly", "no")
    vm_name = params.get("main_vm")

    if vm_name:
        vm = env.get_vm(vm_name)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        params["orig_config_xml"] = vmxml.copy()

    if not libvirt_version.version_compare(1, 0, 0):
        if "--prealloc-metadata" in extra_option:
            test.cancel("metadata preallocation not supported in"
                        " current libvirt version.")
        if incomplete_target:
            test.cancel("It does not support generate target path"
                        "in current libvirt version.")

    pool_type = ['dir', 'disk', 'fs', 'logical', 'netfs', 'iscsi', 'scsi']
    if src_pool_type not in pool_type:
        test.cancel("pool type %s not in supported type list: %s" %
                    (src_pool_type, pool_type))

    # libvirt acl polkit related params
    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")
    uri = params.get("virsh_uri")
    unprivileged_user = params.get('unprivileged_user')
    if unprivileged_user:
        if unprivileged_user.count('EXAMPLE'):
            unprivileged_user = 'testacl'

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

    def attach_disk_encryption(vol_path, uuid, params):
        """
        Attach a disk with luks encryption

        :vol_path: the volume path used in disk XML
        :uuid: the secret uuid of the volume
        :params: the parameter dictionary
        :raise: test.fail when disk cannot be attached

        """
        target_dev = params.get("target_dev", "vdb")
        vol_format = params.get("vol_format", "qcow2")
        disk_path = vol_path
        new_disk_dict = {}
        new_disk_dict.update(
                {"driver_type": vol_format,
                 "source_encryption_dict": {"encryption": 'luks',
                                            "secret": {"type": "passphrase",
                                                       "uuid": uuid}}})
        result = utlv.attach_additional_device(vm_name, target_dev,
                                               disk_path, new_disk_dict)
        if result.exit_status:
            raise test.fail("Attach device %s failed." % target_dev)

    def check_vm_start():
        """
        Start a guest

        :params: the parameter dictionary
        :raise: test.fail when VM cannot be started
        """
        if not vm.is_alive():
            try:
                vm.start()
            except virt_vm.VMStartError as err:
                test.fail("Failed to start VM: %s" % err)

    def create_luks_secret(vol_path):
        """
        Create secret for luks encryption
        :param vol_path. volume path.
        :return: secret id if create successfully.
        """
        sec_xml = secret_xml.SecretXML("no", "yes")
        sec_xml.description = "volume secret"

        sec_xml.usage = 'volume'
        sec_xml.volume = vol_path
        sec_xml.xmltreefile.write()

        ret = virsh.secret_define(sec_xml.xml)
        utlv.check_exit_status(ret)
        # Get secret uuid.
        try:
            encryption_uuid = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                         ret.stdout.strip())[0].lstrip()
        except IndexError as detail:
            test.error("Fail to get newly created secret uuid")
        logging.debug("Secret uuid %s", encryption_uuid)

        # Set secret value.
        encoding = locale.getpreferredencoding()
        secret_string = base64.b64encode('redhat'.encode(encoding)).decode(encoding)
        ret = virsh.secret_set_value(encryption_uuid, secret_string)
        utlv.check_exit_status(ret)

        return encryption_uuid

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
        rst = process.run(process_vol_cmd, ignore_status=True, shell=True)
        if rst.exit_status:
            if "Snapshots of snapshots are not supported" in rst.stderr_text:
                logging.debug("%s is already a snapshot volume", ori_vol_path)
                process_vol_name = os.path.basename(ori_vol_path)
            else:
                logging.error(rst.stderr_text)
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
                test.fail("Can't find volume %s in pool %s"
                          % (vol_name, pool_name))
            # check format in volume xml
            volxml = libvirt_xml.VolXML()
            post_xml = volxml.new_from_vol_dumpxml(vol_name, pool_name)
            logging.debug("Volume %s XML: %s" % (vol_name,
                                                 post_xml.xmltreefile))
            if 'format' in post_xml.keys() and vol_format is not None:
                if post_xml.format != vol_format:
                    test.fail("Volume format %s is not expected"
                              % vol_format + " as defined.")
        else:
            if vol_name in src_volumes:
                test.fail("Find volume %s in pool %s, but expect not"
                          % (vol_name, pool_name))

    fmt_err0 = "Unknown file format '%s'" % vol_format
    fmt_err1 = "Formatting or formatting option not "
    fmt_err1 += "supported for file format '%s'" % vol_format
    fmt_err2 = "Driver '%s' does not support " % vol_format
    fmt_err2 += "image creation"
    fmt_err_list = [fmt_err0, fmt_err1, fmt_err2]
    skip_msg = "Volume format '%s' is not supported by qemu-img" % vol_format
    vol_path_list = []
    secret_uuids = []
    try:
        # Create the src pool
        src_pool_name = "virt-%s-pool" % src_pool_type
        pvt = utlv.PoolVolumeTest(test, params)
        pvt.pre_pool(src_pool_name, src_pool_type, src_pool_target,
                     src_emulated_image, image_size=image_size,
                     source_format=src_pool_format)

        src_pv = libvirt_storage.PoolVolume(src_pool_name)
        src_pool_uuid = libvirt_storage.StoragePool().pool_info(src_pool_name)['UUID']
        # Print current pools for debugging
        logging.debug("Current pools:%s",
                      libvirt_storage.StoragePool().list_pools())
        # Create volumes by virsh in a loop
        while pool_vol_num > 0:
            # Set volume xml file
            vol_name = prefix_vol_name + "_%s" % pool_vol_num
            bad_vol_name = params.get("bad_vol_name", "")
            if bad_vol_name:
                vol_name = bad_vol_name
            pool_vol_num -= 1
            # disk partition for new volume
            if src_pool_type == "disk":
                vol_name = utlv.new_disk_vol_name(src_pool_name)
                if vol_name is None:
                    test.error("Fail to generate volume name")
            if by_xml:
                # According to BZ#1138523, we need inpect the right name
                # (disk partition) for new volume
                if src_pool_type == "disk":
                    vol_name = utlv.new_disk_vol_name(src_pool_name)
                    if vol_name is None:
                        test.error("Fail to generate volume name")
                vol_arg['name'] = vol_name
                volxml = libvirt_xml.VolXML()
                newvol = volxml.new_vol(**vol_arg)
                if luks_encrypted:
                    if vol_format == "qcow2" and not libvirt_version.version_compare(6, 10, 0):
                        test.cancel("Qcow2 format with luks encryption"
                                    " is not supported in current libvirt")
                    # For luks encrypted disk, add related xml in newvol
                    luks_encryption_params = {}
                    luks_encryption_params.update({"format": "luks"})
                    luks_secret_uuid = create_luks_secret(os.path.join(src_pool_target,
                                                                       vol_name))
                    secret_uuids.append(luks_secret_uuid)
                    luks_encryption_params.update({"secret": {"type": encryption_secret_type,
                                                              "uuid": luks_secret_uuid}})
                    newvol.encryption = volxml.new_encryption(**luks_encryption_params)
                vol_xml = newvol['xml']
                if params.get('setup_libvirt_polkit') == 'yes':
                    process.run("chmod 666 %s" % vol_xml, ignore_status=True,
                                shell=True)
                    if luks_encrypted and libvirt_version.version_compare(4, 5, 0):
                        try:
                            polkit = test_setup.LibvirtPolkitConfig(params)
                            polkit_rules_path = polkit.polkit_rules_path
                            with open(polkit_rules_path, 'r+') as f:
                                rule = f.readlines()
                                for index, v in enumerate(rule):
                                    if v.find("secret") >= 0:
                                        nextline = rule[index + 1]
                                        s = nextline.replace("QEMU", "secret").replace(
                                                "pool_name", "secret_uuid").replace(
                                                        "virt-dir-pool", "%s" % luks_secret_uuid)
                                        rule[index + 1] = s
                                rule = ''.join(rule)
                            with open(polkit_rules_path, 'w+') as f:
                                f.write(rule)
                            logging.debug(rule)
                            polkit.polkitd.restart()
                        except IOError as e:
                            logging.error(e)
                # Run virsh_vol_create to create vol
                logging.debug("Create volume from XML: %s" % newvol.xmltreefile)
                cmd_result = virsh.vol_create(
                    src_pool_name, vol_xml, extra_option,
                    unprivileged_user=unprivileged_user, uri=uri,
                    ignore_status=True, debug=True)

                if luks_encrypted and vol_format == "qcow2":
                    vol_path = virsh.vol_path(vol_name,
                                              src_pool_name).stdout.strip()
                    uuid = luks_secret_uuid
                    attach_disk_encryption(vol_path, uuid, params)
                    check_vm_start()

            else:
                # Run virsh_vol_create_as to create_vol
                pool_name = src_pool_name
                if params.get("create_vol_by_pool_uuid") == "yes":
                    pool_name = src_pool_uuid
                cmd_result = virsh.vol_create_as(
                    vol_name, pool_name, vol_capacity, vol_allocation,
                    vol_format, extra_option, unprivileged_user=unprivileged_user,
                    uri=uri, readonly=virsh_readonly_mode, ignore_status=True, debug=True)
            # Check result
            try:
                utlv.check_exit_status(cmd_result, status_error)
                check_vol(src_pool_name, vol_name, not status_error)
                if bad_vol_name:
                    pattern = "volume name '%s' cannot contain '/'" % vol_name
                    logging.debug("pattern: %s", pattern)
                    if "\\" in pattern and by_xml:
                        pattern = pattern.replace("\\", "\\\\")
                    if re.search(pattern, cmd_result.stderr) is None:
                        test.fail("vol-create failed with unexpected reason")
                if not status_error:
                    vol_path = virsh.vol_path(vol_name,
                                              src_pool_name).stdout.strip()
                    logging.debug("Full path of %s: %s", vol_name, vol_path)
                    vol_path_list.append(vol_path)
            except exceptions.TestFail as detail:
                stderr = cmd_result.stderr
                if any(err in stderr for err in fmt_err_list):
                    test.cancel(skip_msg)
                else:
                    test.fail("Create volume fail:\n%s" % detail)
        # Post process vol by other programs
        process_vol_by = params.get("process_vol_by")
        process_vol_type = params.get("process_vol_type", "")
        expect_vol_exist = "yes" == params.get("expect_vol_exist", "yes")
        if process_vol_by and vol_path_list:
            process_vol = post_process_vol(vol_path_list[0])
            if process_vol is not None:
                try:
                    virsh.pool_refresh(src_pool_name, ignore_status=False)
                    check_vol(src_pool_name, process_vol, expect_vol_exist)
                except (process.CmdError, exceptions.TestFail) as detail:
                    if process_vol_type == "thin":
                        logging.error(str(detail))
                        test.cancel("You may encounter bug BZ#1060287")
                    else:
                        test.fail("Fail to refresh pool:\n%s" % detail)
            else:
                test.fail("Post process volume failed")
    finally:
        # Cleanup
        # For old version lvm2(2.02.106 or early), deactivate volume group
        # (destroy libvirt logical pool) will fail if which has deactivated
        # lv snapshot, so before destroy the pool, we need activate it manually
        if vm_name:
            virsh.destroy(vm_name, debug=True, ignore_status=True)
        if params.get("orig_config_xml"):
            params.get("orig_config_xml").sync()
        if src_pool_type == 'logical' and vol_path_list:
            vg_name = vol_path_list[0].split('/')[2]
            process.run("lvchange -ay %s" % vg_name, shell=True)
        try:
            pvt.cleanup_pool(src_pool_name, src_pool_type, src_pool_target,
                             src_emulated_image)
            for secret_uuid in set(secret_uuids):
                virsh.secret_undefine(secret_uuid)
        except exceptions.TestFail as detail:
            logging.error(str(detail))
        if multipathd_status:
            multipathd.start()
