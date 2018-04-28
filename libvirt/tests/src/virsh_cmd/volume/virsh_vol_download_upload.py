import os
import logging
import string
import hashlib
import locale

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import libvirt_xml
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
            # Write date to volume
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

        if result.exit_status != 0:
            test.fail("Fail to %s volume: %s" %
                      (operation, result.stderr))

        # Compare the change part on volume and file
        if ori_digest == aft_digest:
            logging.info("file digests match, volume %s suceed", operation)
        else:
            test.fail("file digests do not match, volume %s failed"
                      % operation)

    finally:
        pvt.cleanup_pool(pool_name, pool_type, pool_target, "volumetest")
        for secret_uuid in set(secret_uuids):
            virsh.secret_undefine(secret_uuid)
        if os.path.isfile(file_path):
            os.remove(file_path)
