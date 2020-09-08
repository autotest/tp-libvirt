import os
import logging
import string
import hashlib
import locale
import aexpect

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import libvirt_xml
from virttest import utils_misc
from virttest import remote
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk

from virttest.utils_test import libvirt
from virttest.utils_test import libvirt as utlv

from virttest import libvirt_version


def digest(path, offset, length):
    """
    Read data from file with length bytes, begin at offset
    and return md5 hexdigest

    :param path: file absolute path to read
    :param offset: offset that begin to read
    :param length: length will read
    :return: md5 result in hex
    """
    with open(path, 'rb') as read_fd:
        read_fd.seek(offset)
        hash_md = hashlib.md5()
        done = 0
        while True:
            want = 1024
            if length and length - done < want:
                want = length - done
            outstr = read_fd.read(want)
            got = len(outstr)
            if got == 0:
                break
            done += got
            hash_md.update(outstr)

    return hash_md.hexdigest()


def write_file(path):
    """
    write 1M test data to file
    """
    logging.info("write data into file %s", path)
    with open(path, 'wb') as write_fd:
        datastr = ''.join(string.ascii_lowercase + string.ascii_uppercase +
                          string.digits + '.' + '\n')
        data = ''.join(16384 * datastr)
        write_fd.write(data.encode(locale.getpreferredencoding()))


def create_luks_vol(pool_name, vol_name, sec_uuid, vol_arg):
    """
    Create a luks volume
    :param pool_name: the pool in which a vol will be created
    :param vol_name: the name of the volume
    :param sec_uuid: secret's uuid to be used for luks encryption
    :param vol_arg: detailed params to create volume
    """
    volxml = libvirt_xml.VolXML()
    newvol = volxml.new_vol(**vol_arg)
    luks_encryption_params = {}
    luks_encryption_params.update({"format": "luks"})
    luks_encryption_params.update({"secret": {"type": "passphrase",
                                              "uuid": sec_uuid}})
    newvol.encryption = volxml.new_encryption(**luks_encryption_params)
    vol_xml = newvol['xml']
    logging.debug("Create volume from XML: %s" % newvol.xmltreefile)
    cmd_result = virsh.vol_create(pool_name, vol_xml, ignore_status=True,
                                  debug=True)


def create_disk(disk_type, disk_path, disk_format, disk_device_type,
                disk_device, disk_target, disk_bus):
    """
    Create another disk for a given path,customize some attributes.

    :param disk_type: the type of disk.
    :param disk_path: the path of disk.
    :param disk_format: the format to disk image.
    :param disk_device_type: the disk device type.
    :param disk_device: the device of disk.
    :param disk_target: the target of disk.
    :param disk_bus: the target bus of disk.
    :return: disk object if created successfully.
    """
    disk_source = libvirt.create_local_disk(disk_type, disk_path, '1', disk_format)
    custom_disk = Disk(type_name=disk_device_type)
    custom_disk.device = disk_device
    source_dict = {'file': disk_source}
    custom_disk.source = custom_disk.new_disk_source(
        **{"attrs": source_dict})
    target_dict = {"dev": disk_target, "bus": disk_bus}
    custom_disk.target = target_dict
    driver_dict = {"name": "qemu", 'type': disk_format}
    custom_disk.driver = driver_dict
    return custom_disk


def write_disk(test, guest_vm, target_name, size=200):
    """
    Write data into disk in VM discard value.

    :param test: test itself.
    :param guest_vm: Guest VM.
    :param target_name: Device target name.
    :param size: Data size in 1M unit.
    """
    logging.info("Write data into disk in VM...")
    try:
        session = guest_vm.wait_for_login()
        cmd = ("fdisk -l  /dev/{0} && mkfs.ext4 -F /dev/{0} && "
               "mkdir -p test && mount /dev/{0} test && "
               "dd if=/dev/urandom of=test/file bs=1M count={1} && sync"
               .format(target_name, size))
        status, output = session.cmd_status_output(cmd)
        if status != 0:
            session.close()
            test.fail("Failed due to: %s" % output.strip())
        session.close()
    except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
        logging.error(str(e))


def run(test, params, env):
    """
    Do test for vol-download and vol-upload

    Basic steps are
    1. Create pool with type defined in cfg
    2. Create image with writing data in it
    3. Get md5 value before operation
    4. Do vol-download/upload with options(offset, length)
    5. Check md5 value after operation
    """

    pool_type = params.get("vol_download_upload_pool_type")
    pool_name = params.get("vol_download_upload_pool_name")
    pool_target = params.get("vol_download_upload_pool_target")
    if os.path.dirname(pool_target) is "":
        pool_target = os.path.join(data_dir.get_tmp_dir(), pool_target)
    vol_name = params.get("vol_download_upload_vol_name")
    file_name = params.get("vol_download_upload_file_name")
    file_path = os.path.join(data_dir.get_tmp_dir(), file_name)
    offset = params.get("vol_download_upload_offset")
    length = params.get("vol_download_upload_length")
    capacity = params.get("vol_download_upload_capacity")
    allocation = params.get("vol_download_upload_allocation")
    frmt = params.get("vol_download_upload_format")
    operation = params.get("vol_download_upload_operation")
    create_vol = ("yes" == params.get("vol_download_upload_create_vol", "yes"))
    setup_libvirt_polkit = "yes" == params.get("setup_libvirt_polkit")
    b_luks_encrypt = "luks" == params.get("encryption_method")
    encryption_password = params.get("encryption_password", "redhat")
    secret_uuids = []
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    sparse_option_support = "yes" == params.get("sparse_option_support", "yes")

    # libvirt acl polkit related params
    uri = params.get("virsh_uri")
    unpri_user = params.get('unprivileged_user')
    if unpri_user:
        if unpri_user.count('EXAMPLE'):
            unpri_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if setup_libvirt_polkit:
            test.error("API acl test not supported in current"
                       " libvirt version.")
    # Destroy VM.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        pvt = utlv.PoolVolumeTest(test, params)
        pvt.pre_pool(pool_name, pool_type, pool_target, "volumetest",
                     pre_disk_vol=["50M"])
        # According to BZ#1138523, we need inpect the right name
        # (disk partition) for new volume
        if pool_type == "disk":
            vol_name = utlv.new_disk_vol_name(pool_name)
            if vol_name is None:
                test.error("Fail to generate volume name")
            # update polkit rule as the volume name changed
            if setup_libvirt_polkit:
                vol_pat = r"lookup\('vol_name'\) == ('\S+')"
                new_value = "lookup('vol_name') == '%s'" % vol_name
                utlv.update_polkit_rule(params, vol_pat, new_value)
        if create_vol:
            if b_luks_encrypt:
                if not libvirt_version.version_compare(2, 0, 0):
                    test.cancel("LUKS format not supported in "
                                "current libvirt version")
                params['sec_volume'] = os.path.join(pool_target, vol_name)
                luks_sec_uuid = utlv.create_secret(params)
                ret = virsh.secret_set_value(luks_sec_uuid,
                                             encryption_password,
                                             encode=True)
                utlv.check_exit_status(ret)
                secret_uuids.append(luks_sec_uuid)
                vol_arg = {}
                vol_arg['name'] = vol_name
                vol_arg['capacity'] = int(capacity)
                vol_arg['allocation'] = int(allocation)
                create_luks_vol(pool_name, vol_name, luks_sec_uuid, vol_arg)
            else:
                pvt.pre_vol(vol_name, frmt, capacity, allocation, pool_name)

        virsh.pool_refresh(pool_name, debug=True)
        vol_list = virsh.vol_list(pool_name, debug=True).stdout.strip()
        # iscsi volume name is different from others
        if pool_type == "iscsi":
            # Due to BZ 1843791, the volume cannot be obtained sometimes.
            if len(vol_list.splitlines()) < 3:
                test.fail("Failed to get iscsi type volume.")
            vol_name = vol_list.split('\n')[2].split()[0]

        vol_path = virsh.vol_path(vol_name, pool_name,
                                  ignore_status=False).stdout.strip()
        logging.debug("vol_path is %s", vol_path)

        # Add command options
        if pool_type is not None:
            options = " --pool %s" % pool_name
        if offset is not None:
            options += " --offset %s" % offset
            offset = int(offset)
        else:
            offset = 0

        if length is not None:
            options += " --length %s" % length
            length = int(length)
        else:
            length = 0
        logging.debug("%s options are %s", operation, options)

        if operation == "upload":
            # write data to file
            write_file(file_path)

            # Set length for calculate the offset + length in the following
            # func get_pre_post_digest() and digest()
            if length == 0:
                length = 1048576

            def get_pre_post_digest():
                """
                Get pre region and post region digest if have offset and length
                :return: pre digest and post digest
                """
                # Get digest of pre region before offset
                if offset != 0:
                    digest_pre = digest(vol_path, 0, offset)
                else:
                    digest_pre = 0
                logging.debug("pre region digest read from %s 0-%s is %s",
                              vol_path, offset, digest_pre)
                # Get digest of post region after offset+length
                digest_post = digest(vol_path, offset + length, 0)
                logging.debug("post region digest read from %s %s-0 is %s",
                              vol_path, offset + length, digest_post)

                return (digest_pre, digest_post)

            # Get pre and post digest before operation for compare
            (ori_pre_digest, ori_post_digest) = get_pre_post_digest()
            ori_digest = digest(file_path, 0, 0)
            logging.debug("ori digest read from %s is %s", file_path,
                          ori_digest)

            if setup_libvirt_polkit:
                process.run("chmod 666 %s" % file_path, ignore_status=True,
                            shell=True)

            # Do volume upload
            result = virsh.vol_upload(vol_name, file_path, options,
                                      unprivileged_user=unpri_user,
                                      uri=uri, debug=True)
            if result.exit_status == 0:
                # Get digest after operation
                (aft_pre_digest, aft_post_digest) = get_pre_post_digest()
                aft_digest = digest(vol_path, offset, length)
                logging.debug("aft digest read from %s is %s", vol_path,
                              aft_digest)

                # Compare the pre and post part before and after
                if ori_pre_digest == aft_pre_digest and \
                   ori_post_digest == aft_post_digest:
                    logging.info("file pre and aft digest match")
                else:
                    test.fail("file pre or post digests do not"
                              "match, in %s", operation)

        if operation == "download":
            # Write data to volume
            write_file(vol_path)

            # Record the digest value before operation
            ori_digest = digest(vol_path, offset, length)
            logging.debug("original digest read from %s is %s", vol_path,
                          ori_digest)

            process.run("touch %s" % file_path, ignore_status=True, shell=True)
            if setup_libvirt_polkit:
                process.run("chmod 666 %s" % file_path, ignore_status=True,
                            shell=True)

            # Do volume download
            result = virsh.vol_download(vol_name, file_path, options,
                                        unprivileged_user=unpri_user,
                                        uri=uri, debug=True)
            if result.exit_status == 0:
                # Get digest after operation
                aft_digest = digest(file_path, 0, 0)
                logging.debug("new digest read from %s is %s", file_path,
                              aft_digest)

        if operation != "mix":
            if result.exit_status != 0:
                test.fail("Fail to %s volume: %s" %
                          (operation, result.stderr))
            # Compare the change part on volume and file
            if ori_digest == aft_digest:
                logging.info("file digests match, volume %s succeed", operation)
            else:
                test.fail("file digests do not match, volume %s failed"
                          % operation)

        if operation == "mix":
            target = params.get("virt_disk_device_target", "vdb")
            disk_file_path = os.path.join(pool_target, file_name)

            # Create one disk xml and attach it to VM.
            custom_disk_xml = create_disk('file', disk_file_path, 'raw', 'file',
                                          'disk', target, 'virtio')
            ret = virsh.attach_device(vm_name, custom_disk_xml.xml,
                                      flagstr="--config", debug=True)
            libvirt.check_exit_status(ret)
            if vm.is_dead():
                vm.start()

            # Write 100M data into disk.
            data_size = 100
            write_disk(test, vm, target, data_size)
            data_size_in_bytes = data_size * 1024 * 1024

            # Refresh directory pool.
            virsh.pool_refresh(pool_name, debug=True)

            # Download volume to local with sparse option.
            download_spare_file = "download-sparse.raw"
            download_file_path = os.path.join(data_dir.get_tmp_dir(), download_spare_file)
            options += " --sparse"
            result = virsh.vol_download(file_name, download_file_path, options,
                                        unprivileged_user=unpri_user,
                                        uri=uri, debug=True)

            #Check download image size.
            one_g_in_bytes = 1073741824
            download_img_info = utils_misc.get_image_info(download_file_path)
            download_disk_size = int(download_img_info['dsize'])
            if (download_disk_size < data_size_in_bytes or
               download_disk_size >= one_g_in_bytes):
                test.fail("download image size:%d is less than the generated "
                          "data size:%d or greater than or equal to 1G."
                          % (download_disk_size, data_size_in_bytes))

            # Create one upload sparse image file.
            upload_sparse_file = "upload-sparse.raw"
            upload_file_path = os.path.join(pool_target, upload_sparse_file)
            libvirt.create_local_disk('file', upload_file_path, '1', 'raw')

            # Refresh directory pool.
            virsh.pool_refresh(pool_name, debug=True)
            # Do volume upload, upload sparse file which download last time.
            result = virsh.vol_upload(upload_sparse_file, download_file_path, options,
                                      unprivileged_user=unpri_user,
                                      uri=uri, debug=True)
            upload_img_info = utils_misc.get_image_info(upload_file_path)
            upload_disk_size = int(upload_img_info['dsize'])
            if (upload_disk_size < data_size_in_bytes or
               upload_disk_size >= one_g_in_bytes):
                test.fail("upload image size:%d is less than the generated "
                          "data size:%d or greater than or equal to 1G."
                          % (upload_disk_size, data_size_in_bytes))
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        pvt.cleanup_pool(pool_name, pool_type, pool_target, "volumetest")
        for secret_uuid in set(secret_uuids):
            virsh.secret_undefine(secret_uuid)
        if os.path.isfile(file_path):
            os.remove(file_path)
