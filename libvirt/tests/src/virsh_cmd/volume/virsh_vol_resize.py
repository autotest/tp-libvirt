import re
import logging
import os
import base64
import locale

from avocado.utils import process
from avocado.core import exceptions

from virttest import libvirt_storage
from virttest import utils_misc
from virttest import virsh
from virttest import libvirt_xml
from virttest.utils_test import libvirt
from virttest.libvirt_xml import secret_xml

from virttest import libvirt_version


def get_expect_info(new_capacity, vol_path, test, resize_option=None):
    """
    Get the expect volume capacity and allocation size, for comparation
    after volume resize. As virsh vol-info return imprecise values, so we
    need get volume info from qemu side. The process is:
    1) Transform new capacity size to bytes.
    2) Get image info by qemu-img info(byte size).
    3) Calculate the expect info according to volume resie option.

    :param new_capacity: New capacity for the vol, as scaled integer
    :param vol_path: Absolute path of volume
    :return: Expect volume capacity and allocation
    """
    if new_capacity.isdigit():
        # Default bytes
        new_capacity = new_capacity + "b"

    suffixes_list1 = ['B', 'K', 'KIB', 'M', 'MIB', 'G', 'GIB', 'T', 'TIB']
    suffixes_list2 = ['KB', 'MB', 'GB', 'TB']
    expect_info = {}
    suffix = "B"
    factor = "1024"
    try:
        suffix = re.findall(r"[\s\d](\D+)", new_capacity, re.I)[-1].strip()
    except IndexError:
        test.error("Incorrect size format %s." % new_capacity)
    if suffix in suffixes_list1:
        factor = "1024"
    elif suffix in suffixes_list2:
        factor = "1000"
    else:
        test.error("Unsupport size unit '%s'." % suffix)

    try:
        # Transform the size to bytes
        new_size = utils_misc.normalize_data_size(new_capacity, "B", factor)

        # Get image info
        img_info = utils_misc.get_image_info(vol_path)

        # Init expect_info
        expect_info['Capacity'] = img_info['vsize']
        expect_info['Allocation'] = img_info['dsize']

        # Deal with resize options
        if not resize_option:
            expect_info['Capacity'] = int(float(new_size))
            return expect_info
        support_options = ["--allocate", "--delta", "--shrink"]
        find_delt = False
        find_allo = False
        for option in resize_option.split():
            logging.debug("Find '%s' in volume resize option", option)
            if option not in support_options:
                # Give an invalid option is acceptable in the test, so just
                # output debug log
                logging.debug("Invalid resize option: %s.", option)
                return expect_info
            if option == "--shrink":
                # vol-resize --shrink has a bug now, so output error
                logging.error("Shrink volume not support in this test.")
                return expect_info
            if option == "--allocate":
                find_allo = True
                logging.debug("Allocate the new capacity, rather than "
                              "leaving it sparse.")
            if option == "--delta":
                find_delt = True
                logging.debug("Use capacity as a delta to current size, "
                              "rather than the new size")
        if find_allo and find_delt:
            expect_info['Capacity'] += int(float(new_size))
            expect_info['Allocation'] += int(float(new_size))
        elif find_allo:
            expect_info['Capacity'] = int(float(new_size))
            expect_info['Allocation'] += int(float(new_size)) - img_info['vsize']
        elif find_delt:
            expect_info['Capacity'] += int(float(new_size))
        else:
            pass
        return expect_info
    except (IndexError, ValueError) as detail:
        test.error("Fail to get expect volume info:\n%s" % detail)


def create_luks_secret(vol_path, test):
    """
    Create secret for luks encryption
    :param vol_path: volume path.
    :return: secret id if create successfully.
    """
    sec_xml = secret_xml.SecretXML("no", "yes")
    sec_xml.description = "volume secret"

    sec_xml.usage = 'volume'
    sec_xml.volume = vol_path
    sec_xml.xmltreefile.write()

    ret = virsh.secret_define(sec_xml.xml)
    libvirt.check_exit_status(ret)
    try:
        encryption_uuid = re.findall(r".+\S+(\ +\S+)\ +.+\S+",
                                     ret.stdout.strip())[0].lstrip()
    except IndexError:
        test.error("Fail to get newly created secret uuid")
    logging.debug("Secret uuid %s", encryption_uuid)
    return encryption_uuid


def set_secret_value(password, encryption_uuid):
    """
    Generate secret string and set secret value.

    :param password: password for encryption
    :param encryption_uuid: uuid of secret
    """
    encoding = locale.getpreferredencoding()
    secret_string = base64.b64encode(password.encode(encoding)).decode(encoding)
    ret = virsh.secret_set_value(encryption_uuid, secret_string)
    libvirt.check_exit_status(ret)


def create_luks_vol(vol_name, sec_uuid, params, test):
    """
    Create a luks volume
    :param vol_name: the name of the volume
    :param sec_uuid: secret's uuid to be used for luks encryption
    :param params: detailed params to create volume
    :param test: test object
    """
    pool_name = params.get("pool_name")
    extra_option = params.get("extra_option", "")
    unprivileged_user = params.get('unprivileged_user')
    uri = params.get("virsh_uri")
    vol_arg = {}
    for key in list(params.keys()):
        if (key.startswith('vol_') and not key.startswith('vol_new')):
            if key[4:] in ['capacity', 'allocation']:
                vol_arg[key[4:]] = int(float(utils_misc.normalize_data_size(params[key],
                                                                            "B", 1024)))
            elif key[4:] in ['owner', 'group']:
                vol_arg[key[4:]] = int(params[key])
            else:
                vol_arg[key[4:]] = params[key]
                if vol_arg[key[4:]] == "qcow2" and not libvirt_version.version_compare(6, 10, 0):
                    test.cancel("Qcow2 format with luks encryption is not"
                                " supported in current libvirt version")
    vol_arg['name'] = vol_name
    volxml = libvirt_xml.VolXML()
    newvol = volxml.new_vol(**vol_arg)
    luks_encryption_params = {}
    luks_encryption_params.update({"format": "luks"})
    luks_encryption_params.update({"secret": {"type": "passphrase",
                                              "uuid": sec_uuid}})
    newvol.encryption = volxml.new_encryption(**luks_encryption_params)
    vol_xml = newvol['xml']
    if params.get('setup_libvirt_polkit') == 'yes':
        process.run("chmod 666 %s" % vol_xml, ignore_status=True,
                    shell=True)
    logging.debug("Create volume from XML: %s" % newvol.xmltreefile)
    cmd_result = virsh.vol_create(pool_name, vol_xml, extra_option,
                                  unprivileged_user=unprivileged_user, uri=uri,
                                  ignore_status=True, debug=True)


def check_vol_info(pool_vol, vol_name, test, expect_info=None):
    """
    Check the volume info, or/and compare with the expect_info.

    :params pool_vol: Instance of PoolVolume.
    :params vol_name: Name of the volume.
    :params expect_info: Expect volume info for comparation.
    """
    vol_info = pool_vol.volume_info(vol_name)
    for key in vol_info:
        logging.debug("Volume info: %s = %s", key, vol_info[key])
    if not expect_info:
        return True
    else:
        check_capacity_pass = True
        check_allocation_pass = True
        try:
            # Get image info
            vol_path = pool_vol.list_volumes()[vol_name]
            img_info = utils_misc.get_image_info(vol_path)
            if expect_info['Capacity'] != img_info['vsize']:
                logging.debug("Capacity(Virtual size) is %s bytes",
                              img_info['vsize'])
                logging.error("Volume capacity not equal to expect value %s",
                              expect_info['Capacity'])
                check_capacity_pass = False
            if expect_info['Allocation'] != img_info['dsize']:
                logging.debug("Allocation(Disk size) is %s bytes",
                              img_info['dsize'])
                logging.error("Volume Allocation not equal to expect value %s",
                              expect_info['Allocation'])
                check_allocation_pass = False
            return check_capacity_pass & check_allocation_pass
        except KeyError as detail:
            test.error("Fail to check volume info:\n%s" % detail)


def run(test, params, env):
    """
    Test command: virsh vol-resize

    Resize the capacity of the given volume (default bytes).
    1. Define and start a given type pool.
    2. Create a volume in the pool.
    3. Do vol-resize.
    4. Check the volume info.
    5. Delete the volume and pool.

    TODO:
    Add volume shrink test after libvirt uptream support it.
    """

    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target")
    emulated_image = params.get("emulated_image")
    emulated_image_size = params.get("emulated_image_size")
    vol_name = params.get("vol_name")
    vol_format = params.get("vol_format")
    vol_capacity = params.get("vol_capacity")
    vol_new_capacity = params.get("vol_new_capacity")
    resize_option = params.get("resize_option", "")
    check_vol_size = "yes" == params.get("check_vol_size", "yes")
    status_error = "yes" == params.get("status_error", "no")
    b_luks_encrypt = "luks" == params.get("encryption_method")
    encryption_password = params.get("encryption_password", "redhat")
    secret_uuids = []

    if not libvirt_version.version_compare(1, 0, 0):
        if "--allocate" in resize_option:
            test.cancel("'--allocate' flag is not supported in"
                        " current libvirt version.")

    # libvirt acl polkit related params
    uri = params.get("virsh_uri")
    unpri_user = params.get('unprivileged_user')
    if unpri_user:
        if unpri_user.count('EXAMPLE'):
            unpri_user = 'testacl'

    if not libvirt_version.version_compare(1, 1, 1):
        if params.get('setup_libvirt_polkit') == 'yes':
            test.cancel("API acl test not supported in current"
                        " libvirt version.")

    libv_pvt = libvirt.PoolVolumeTest(test, params)
    try:
        libv_pool = libvirt_storage.StoragePool()
        # Raise error if given name pool already exist
        if libv_pool.pool_exists(pool_name):
            test.error("Pool '%s' already exist", pool_name)
        else:
            # Create a new pool
            libv_pvt.pre_pool(pool_name, pool_type, pool_target,
                              emulated_image, image_size=emulated_image_size)
            pool_info = libv_pool.pool_info(pool_name)
            for key in pool_info:
                logging.debug("Pool info: %s = %s", key, pool_info[key])
            # Deal with vol_new_capacity, '--capacity' only accpet integer
            if vol_new_capacity == "pool_available":
                pool_avai = pool_info["Available"].split()
                vol_new_capacity = pool_avai[0].split('.')[0] + pool_avai[1]
            if vol_new_capacity == "pool_capacity":
                pool_capa = pool_info["Capacity"].split()
                vol_new_capacity = pool_capa[0].split('.')[0] + pool_capa[1]

        # Create a volume
        if b_luks_encrypt:
            luks_sec_uuid = create_luks_secret(os.path.join(pool_target, vol_name),
                                               test)
            secret_uuids.append(luks_sec_uuid)
            set_secret_value(encryption_password, luks_sec_uuid)
            create_luks_vol(vol_name, luks_sec_uuid, params, test)
        else:
            libv_pvt.pre_vol(vol_name=vol_name, vol_format=vol_format,
                             capacity=vol_capacity, allocation=None,
                             pool_name=pool_name)
        libv_vol = libvirt_storage.PoolVolume(pool_name)
        check_vol_info(libv_vol, vol_name, test)

        # The volume size may not accurate as we expect after resize, such as:
        # 1) vol_new_capacity = 1b with --delta option, the volume size will not
        #    change; run
        # 2) vol_new_capacity = 1KB with --delta option, the volume size will
        #    increase 1024 not 1000
        # So we can disable volume size check after resize
        if check_vol_size:
            vol_path = libv_vol.list_volumes()[vol_name]
            expect_info = get_expect_info(vol_new_capacity, vol_path, test,
                                          resize_option)
            logging.debug("Expect volume info: %s", expect_info)
        else:
            expect_info = {}

        # Run vol-resize
        result = virsh.vol_resize(vol_name, vol_new_capacity, pool_name,
                                  resize_option, uri=uri,
                                  unprivileged_user=unpri_user,
                                  debug=True)
        if not status_error:
            if result.exit_status != 0:
                test.fail(result.stdout.strip() + result.stderr.strip())
            else:
                if check_vol_info(libv_vol, vol_name, test, expect_info):
                    logging.debug("Volume %s resize check pass.", vol_name)
                else:
                    test.fail("Volume %s resize check fail." %
                              vol_name)
        elif result.exit_status == 0:
            test.fail("Expect resize fail but run successfully.")
    finally:
        # Clean up
        try:
            libv_pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                  emulated_image)
            for secret_uuid in set(secret_uuids):
                virsh.secret_undefine(secret_uuid)
        except exceptions.TestFail as detail:
            logging.error(str(detail))
