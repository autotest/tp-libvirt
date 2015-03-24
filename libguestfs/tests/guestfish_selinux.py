from autotest.client.shared import error, utils
from virttest import utils_test, utils_misc, data_dir
from virttest.tests import unattended_install
from virttest import virsh, qemu_storage
import logging
import shutil
import os
import re
import commands
import time


def prepare_image(params):
    """
    (1) Check image exits
    (2) Create a image
    (3) Create file system on the image
    """
    image_path = params.get("image_full_path")
    partition_type = params.get("partition_type")
    if params["gf_create_img_force"] == "no" and os.path.exists(image_path):
        params["image_path"] = image_path
        # get mount_point
        if partition_type == 'lvm':
            pv_name = params.get("pv_name", "/dev/sdb")
            vg_name = params.get("vg_name", "vol_test")
            lv_name = params.get("lv_name", "vol_file")
            mount_point = "/dev/%s/%s" % (vg_name, lv_name)
        elif partition_type == "physical":
            logging.info("create physical partition...")
            pv_name = params.get("pv_name", "/dev/sdb")
            mount_point = pv_name + "1"
        params["mount_point"] = mount_point

        logging.debug("Skip preparing image, " + image_path + " exists")
    else:

        params["image_path"] = utils_test.libguestfs.preprocess_image(params)

        if not params.get("image_path"):
            raise error.TestFail("Image could not be created for some reason.")

        gf = utils_test.libguestfs.GuestfishTools(params)
        status, output = gf.create_fs()
        if status is False:
            gf.close_session()
            raise error.TestFail(output)
        gf.close_session()


def test_set_get_selinux(vm, params):
    """
    Test command set_selinux and get_selinux:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)
    gf.set_selinux("1")
    gf_result = gf.get_selinux()
    exp_true = gf_result.stdout.strip()
    gf.set_selinux("0")
    gf_result = gf.get_selinux()
    exp_false = gf_result.stdout.strip()
    if exp_true != 'true' or exp_false != 'false':
        gf.close_session()
        logging.error(exp_true)
        logging.error(exp_false)
        raise error.TestFail("test_set_get_selinux failed")
    gf.close_session()


def test_getcon(vm, params):
    """
    Test command getcon:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)
    gf.set_selinux("1")
    gf.run()
    gf_result = gf.getcon()
    if gf_result.exit_status != 0:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail("getcon failed")
    gf.close_session()


def test_setcon(vm, params):
    """
    Test command setcon:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)
    gf.set_selinux("1")
    gf.run()
    context = "unconfined_u:unconfined_r:unconfined_t:s0"
    gf.setcon(context)
    gf_result = gf.getcon()

    if gf_result.stdout.strip() != context:
        gf.close_session()
        logging.error(gf_result)
        raise error.TestFail("setcon failed")
    gf.close_session()


def run(test, params, env):
    """
    Test of built-in fs_attr_ops related commands in guestfish.

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
    fs_type = params.get("fs_type")
    image_formats = params.get("image_formats")
    image_name = params.get("image_name", "gs_common")

    for image_format in re.findall("\w+", image_formats):
        params["image_format"] = image_format
        for partition_type in re.findall("\w+", partition_types):
            params["partition_type"] = partition_type
            image_dir = params.get("img_dir", data_dir.get_tmp_dir())
            image_path = image_dir + '/' + image_name + '.' + image_format
            image_name_with_fs_pt = image_name + '.' + fs_type + '.' + partition_type
            params['image_name'] = image_name_with_fs_pt
            image_path = image_dir + '/' + image_name_with_fs_pt + '.' + image_format
            params['image_full_path'] = image_path

            prepare_image(params)
            testcase(vm, params)
