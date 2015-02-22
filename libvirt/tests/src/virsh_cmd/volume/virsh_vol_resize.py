import re
import logging
from autotest.client.shared import error
from virttest.utils_test import libvirt
from virttest import libvirt_storage
from virttest import utils_misc
from virttest import virsh
from provider import libvirt_version


def get_expect_info(new_capacity, vol_path, resize_option=None):
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
        raise error.TestError("Incorrect size format %s." % new_capacity)
    if suffix in suffixes_list1:
        factor = "1024"
    elif suffix in suffixes_list2:
        factor = "1000"
    else:
        raise error.TestError("Unsupport size unit '%s'." % suffix)

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
    except (IndexError, ValueError), detail:
        raise error.TestError("Fail to get expect volume info:\n%s" % detail)


def check_vol_info(pool_vol, vol_name, expect_info=None):
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
        except KeyError, detail:
            raise error.TestError("Fail to check volume info:\n%s" % detail)


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

    libv_pvt = libvirt.PoolVolumeTest(test, params)
    try:
        libv_pool = libvirt_storage.StoragePool()
        pool_rename_times = 0
        # Rename pool if given name pool exist, the max rename times is 5
        while libv_pool.pool_exists(pool_name) and pool_rename_times < 5:
            logging.debug("Pool '%s' already exist.", pool_name)
            pool_name = pool_name + "_t"
            logging.debug("Using a new name '%s' to define pool.", pool_name)
            pool_rename_times += 1
        else:
            # Create a new pool
            libv_pvt.pre_pool(pool_name, pool_type, pool_target,
                              emulated_image, emulated_image_size)
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
        libv_pvt.pre_vol(vol_name=vol_name, vol_format=vol_format,
                         capacity=vol_capacity, allocation=None,
                         pool_name=pool_name)
        libv_vol = libvirt_storage.PoolVolume(pool_name)
        check_vol_info(libv_vol, vol_name)

        # The volume size may not accurate as we expect after resize, such as:
        # 1) vol_new_capacity = 1b with --delta option, the volume size will not
        #    change; run
        # 2) vol_new_capacity = 1KB with --delta option, the volume size will
        #    increase 1024 not 1000
        # So we can disable volume size check after resize
        if check_vol_size:
            vol_path = libv_vol.list_volumes()[vol_name]
            expect_info = get_expect_info(vol_new_capacity, vol_path,
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
                raise error.TestFail(result.stdout.strip())
            else:
                if check_vol_info(libv_vol, vol_name, expect_info):
                    logging.debug("Volume %s resize check pass.", vol_name)
                else:
                    raise error.TestFail("Volume %s resize check fail." %
                                         vol_name)
        elif result.exit_status == 0:
            raise error.TestFail("Expect resize fail but run successfully.")
    finally:
        # Clean up
        try:
            libv_pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                  emulated_image)
        except error.TestFail, detail:
            logging.error(str(detail))
