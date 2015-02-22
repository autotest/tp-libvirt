import os
import logging
import string
import hashlib
from autotest.client.shared import utils, error
from virttest import virsh
from virttest.utils_test import libvirt as utlv
from provider import libvirt_version


def digest(path, offset, length):
    """
    Read data from file with length bytes, begin at offset
    and return md5 hexdigest

    :param path: file absolute path to read
    :param offset: offset that begin to read
    :param length: length will read
    :return: md5 result in hex
    """
    read_fd = open(path, 'rb')
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

    read_fd.close()
    return hash_md.hexdigest()


def write_file(path):
    """
    write 1M test data to file
    """
    logging.info("write data into file %s", path)
    write_fd = open(path, 'wb')
    datastr = ''.join(string.lowercase + string.uppercase +
                      string.digits + '.' + '\n')
    data = ''.join(16384 * datastr)
    write_fd.write(data)
    write_fd.close()


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
        pool_target = os.path.join(test.tmpdir, pool_target)
    vol_name = params.get("vol_download_upload_vol_name")
    file_name = params.get("vol_download_upload_file_name")
    file_path = os.path.join(test.tmpdir, file_name)
    offset = params.get("vol_download_upload_offset")
    length = params.get("vol_download_upload_length")
    capacity = params.get("vol_download_upload_capacity")
    allocation = params.get("vol_download_upload_allocation")
    frmt = params.get("vol_download_upload_format")
    operation = params.get("vol_download_upload_operation")
    create_vol = ("yes" == params.get("vol_download_upload_create_vol", "yes"))

    # libvirt acl polkit related params
    uri = params.get("virsh_uri")
    unpri_user = params.get('unprivileged_user')
    if unpri_user:
        if unpri_user.count('EXAMPLE'):
            unpri_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            raise error.TestNAError("API acl test not supported in current"
                                    " libvirt version.")

    try:
        pvt = utlv.PoolVolumeTest(test, params)
        pvt.pre_pool(pool_name, pool_type, pool_target, "volumetest",
                     pre_disk_vol=["50M"])
        if create_vol:
            pvt.pre_vol(vol_name, frmt, capacity, allocation, pool_name)

        vol_list = virsh.vol_list(pool_name).stdout.strip()
        # iscsi volume name is different from others
        if pool_type == "iscsi":
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
            # write date to file
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

            if params.get('setup_libvirt_polkit') == 'yes':
                utils.run("chmod 666 %s" % file_path)

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
                    raise error.TestFail("file pre or post digests do not"
                                         "match, in %s", operation)

        if operation == "download":
            # Write date to volume
            if pool_type == "disk":
                utils.run("mkfs.ext3 -F %s" % vol_path)
            write_file(vol_path)

            # Record the digest value before operation
            ori_digest = digest(vol_path, offset, length)
            logging.debug("original digest read from %s is %s", vol_path,
                          ori_digest)

            utils.run("touch %s" % file_path)
            if params.get('setup_libvirt_polkit') == 'yes':
                utils.run("chmod 666 %s" % file_path)

            # Do volume download
            result = virsh.vol_download(vol_name, file_path, options,
                                        unprivileged_user=unpri_user,
                                        uri=uri, debug=True)
            if result.exit_status == 0:
                # Get digest after operation
                aft_digest = digest(file_path, 0, 0)
                logging.debug("new digest read from %s is %s", file_path,
                              aft_digest)

        if result.exit_status != 0:
            raise error.TestFail("Fail to %s volume: %s" %
                                 (operation, result.stderr))

        # Compare the change part on volume and file
        if ori_digest == aft_digest:
            logging.info("file digests match, volume %s suceed", operation)
        else:
            raise error.TestFail("file digests do not match, volume %s failed",
                                 operation)

    finally:
        pvt.cleanup_pool(pool_name, pool_type, pool_target, "volumetest")
        if os.path.isfile(file_path):
            os.remove(file_path)
