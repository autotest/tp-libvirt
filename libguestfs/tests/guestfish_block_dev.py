from autotest.client.shared import error, utils
from virttest import utils_test, utils_misc, data_dir
from virttest.tests import unattended_install
import logging
import shutil
import os


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

    for image_format in image_formats.split(" "):
        params["image_format"] = image_format
        for partition_type in partition_types.split(" "):
            params["partition_type"] = partition_type
            for fs_type in fs_types.split(" "):
                params["fs_type"] = fs_type
                prepare_image(params)
                testcase(vm, params)
