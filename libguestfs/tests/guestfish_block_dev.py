from autotest.client.shared import error, utils
from virttest import utils_test, utils_misc, data_dir
from virttest.tests import unattended_install
import logging
import shutil
import os
import re


def prepare_image(params):
    """
    (1) Create a image
    (2) Create file system on the image
    """
    params["image_path"] = utils_test.libguestfs.preprocess_image(params)

    if not params.get("image_path"):
        raise error.TestFail("Image could not be created for some reason.")

    gf = utils_test.libguestfs.GuestfishTools(params)
    status, output = gf.create_fs()
    if status is False:
        gf.close_session()
        raise error.TestFail(output)
    gf.close_session()


def test_blockdev_flushbufs(vm, params):
    """
    Test blockdev-flushbufs command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_flushbufs(pv_name)
    logging.debug(gf_result)
    gf.do_mount("/")
    gf.touch("/test")
    gf.ll("/")
    if gf_result.exit_status:
        gf.close_session()
        raise error.TestFail("blockdev_flushbufs failed.")
    logging.info("Get readonly status successfully.")
    gf.close_session()


def test_blockdev_set_get_ro_rw(vm, params):
    """
    Set block device to read-only or read-write

    (1)blockdev-setro
    (2)blockdev-getro
    (3)blockdev-setrw
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")
    gf_result = []
    expect_result = ['false', 'true', 'false']

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")

    gf_result.append(gf.blockdev_getro(pv_name).stdout.strip())
    gf.blockdev_setro(pv_name)
    gf_result.append(gf.blockdev_getro(pv_name).stdout.strip())
    gf.blockdev_setrw(pv_name)
    gf_result.append(gf.blockdev_getro(pv_name).stdout.strip())

    try:
        if gf_result != expect_result:
            raise error.TestFail("Get the uncorrect status, test failed.")
    finally:
        gf.close_session()


def test_blockdev_getsz(vm, params):
    """
    Test command blockdev_getsz
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_getsz(pv_name).stdout.strip()
    gf.close_session()

    get_size = (int(gf_result) * 512)
    power = {'G': 1024 ** 3, "M": 1024 ** 2, "K": 1024}
    image_size = params["image_size"]
    image_size = int(image_size[:-1]) * power[image_size[-1]]
    logging.debug("Image size is %d" % image_size)
    logging.debug("Get size is %d" % get_size)

    if image_size != get_size:
        raise error.TestFail("Can not get the correct result.")


def test_blockdev_getbsz(vm, params):
    """
    Test command blockdev_getbsz
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_getbsz(pv_name).stdout.strip()
    gf.close_session()

    logging.debug("Block size of %s is %d" % (pv_name, int(gf_result)))

    if int(gf_result) != 4096:
        raise error.TestFail("Block size should be 4096")


def test_blockdev_getss(vm, params):
    """
    Test command blockdev_getbss
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_getss(pv_name).stdout.strip()
    gf.close_session()

    logging.debug("Size of %s is %d" % (pv_name, int(gf_result)))

    if int(gf_result) < 512:
        raise error.TestFail("Size of sectors is uncorrect")


def test_blockdev_getsize64(vm, params):
    """
    Test command blockdev_getsize64
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_getsize64(pv_name).stdout.strip()
    gf.close_session()

    get_size = int(gf_result)
    power = {'G': 1024 ** 3, "M": 1024 ** 2, "K": 1024}
    image_size = params["image_size"]
    image_size = int(image_size[:-1]) * power[image_size[-1]]
    logging.debug("Image size is %d" % image_size)
    logging.debug("Get size is %d" % get_size)

    if image_size != get_size:
        raise error.TestFail("Can not get the correct result.")


def test_blockdev_rereadpt(vm, params):
    """
    Test command blockdev-rereadpt
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        # add three disks here
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    pv_name = params.get("pv_name")
    gf.run()

    try:
        result = gf.blockdev_rereadpt(pv_name)
        if result.exit_status:
                raise error.TestFail("blockdev-rereadpt execute failed")
    finally:
        gf.close_session()


def test_canonical_device_name(vm, params):
    """
    Test command canonical_device_name
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    pv_name = params.get("pv_name")

    gf_result = gf.list_filesystems().stdout.strip()
    partition_name = gf_result.split(":")[0]
    gf_result = gf.is_lv(partition_name).stdout.strip()
    if gf_result == "true":
        gf_result = []
        vg, lv = partition_name.split("/")[-2:]
        irregularity = '/dev/mapper/' + vg + '-' + lv
        gf_result.append(gf.canonical_device_name(irregularity).stdout.strip())
        logging.debug("canonical device name is %s" % gf_result)
    else:
        part = partition_name.split("/")
        gf_result = []
        for i in ['h', 'v']:
            part[-1] = i + part[-1][1:]
            irregularity = "/".join(part)
            s = gf.canonical_device_name(irregularity).stdout.strip()
            gf_result.append(s)
        logging.debug("canonical device name is %s" % gf_result)

    gf.close_session()

    for i in gf_result:
        if partition_name != i:
            raise error.TestFail("Can not get the correct result.")


def run(test, params, env):
    """
    Test of built-in block_dev related commands in guestfish.

    1) Get parameters for test
    2) Set options for commands
    3) Run key commands:
       a.add disk or domain with readonly or not
       b.launch
       c.mount root device
    4) Write a file to help result checking
    5) Check result
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    if vm.is_alive():
        vm.destroy()

    operation = params.get("guestfish_function")
    testcase = globals()["test_%s" % operation]
    partition_types = params.get("partition_types")
    fs_types = params.get("fs_types")
    image_formats = params.get("image_formats")

    for image_format in re.findall("\w+", image_formats):
        params["image_format"] = image_format
        for partition_type in re.findall("\w+", partition_types):
            params["partition_type"] = partition_type
            prepare_image(params)
            testcase(vm, params)
