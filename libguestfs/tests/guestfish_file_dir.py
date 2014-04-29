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

    tarball_file = params.get("tarball_file")
    if tarball_file:
        tarball_path = os.path.join(data_dir.get_data_dir(), "tarball",
                                    tarball_file)
    params["tarball_path"] = tarball_path

    gf = utils_test.libguestfs.GuestfishTools(params)
    status, output = gf.create_fs()
    if status is False:
        gf.close_session()
        raise error.TestFail(output)
    gf.close_session()


def test_chmod(vm, params):
    """
    Test chmod command
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
    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')
    gf_result = gf.ll("/file_ops/file_ascii")
    ori_perm_oct = gf_result.stdout.split()[0]
    perm = list(ori_perm_oct)
    i = 0
    while i < len(perm):
        if perm[i] == '-':
            perm[i] = '0'
        else:
            perm[i] = '1'
        i += 1
    ori_perm = oct(int(''.join(perm), 2))
    perm = "0700"
    gf.chmod(perm, "/file_ops/file_ascii")
    mask = '0' + bin(int(perm, 8))[2:]
    new_perm = list('-rwxrwxrwx')
    i = 0
    while i < len(mask):
        if mask[i] == '0':
            new_perm[i] = '-'
        i += 1
    new_perm = ''.join(new_perm)
    gf_result = gf.ll("/file_ops/file_ascii")
    if gf_result.stdout.split()[0] != new_perm:
        gf.close_session()
        raise error.TestFail("chmod failed.")
    gf.chmod(str(ori_perm), "/file_ops/file_ascii")
    gf_result = gf.ll("/file_ops/file_ascii")
    if gf_result.stdout.split()[0] != ori_perm_oct:
        gf.close_session()
        raise error.TestFail("chmod failed.")
    if gf_result.exit_status:
        gf.close_session()
        raise error.TestFail("chmod failed.")
    logging.info("Get readonly status successfully.")
    gf.close_session()


def test_chown(vm, params):
    """
    Test chown command
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
    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')
    gf_result = gf.ll("/file_ops/file_ascii")
    logging.debug(gf_result.stdout)
    gf.chown(100, 100, "/file_ops/file_ascii")
    gf_result = gf.ll("/file_ops/file_ascii")
    if gf_result.stdout.split()[2] != '100' or gf_result.stdout.split()[3] != 'users':
        gf.close_session()
        raise error.TestFail("chown failed.")
    gf.chown(500, 500, "/file_ops/file_ascii")
    gf_result = gf.ll("/file_ops/file_ascii")
    if gf_result.stdout.split()[2] != '500' or gf_result.stdout.split()[3] != '500':
        gf.close_session()
        raise error.TestFail("chown failed.")
    gf.chown(100, 100, "/file_ops/file_ascii_softlink")
    gf_result = gf.ll("/file_ops/file_ascii_softlink")
    if gf_result.stdout.split()[2] != 'root' or gf_result.stdout.split()[3] != 'root':
        gf.close_session()
        raise error.TestFail("chown failed.")
    gf.chown(500, 500, "/file_ops/file_ascii_softlink")
    gf_result = gf.ll("/file_ops/file_ascii_softlink")
    if gf_result.stdout.split()[2] != 'root' or gf_result.stdout.split()[3] != 'root':
        gf.close_session()
        raise error.TestFail("chown failed.")
    gf.close_session()


def test_lchown(vm, params):
    """
    Test lchown command
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
    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')
    gf_result = gf.ll("/file_ops/file_ascii")
    logging.debug(gf_result.stdout)
    gf.lchown(100, 100, "/file_ops/file_ascii")
    gf_result = gf.ll("/file_ops/file_ascii")
    if gf_result.stdout.split()[2] != '100' or gf_result.stdout.split()[3] != 'users':
        gf.close_session()
        raise error.TestFail("lchown failed.")
    gf.lchown(500, 500, "/file_ops/file_ascii")
    gf_result = gf.ll("/file_ops/file_ascii")
    if gf_result.stdout.split()[2] != '500' or gf_result.stdout.split()[3] != '500':
        gf.close_session()
        raise error.TestFail("lchown failed.")
    gf.lchown(100, 100, "/file_ops/file_ascii_softlink")
    gf_result = gf.ll("/file_ops/file_ascii_softlink")
    if gf_result.stdout.split()[2] != '100' or gf_result.stdout.split()[3] != 'users':
        gf.close_session()
        raise error.TestFail("lchown failed.")
    gf.lchown(500, 500, "/file_ops/file_ascii_softlink")
    gf_result = gf.ll("/file_ops/file_ascii_softlink")
    if gf_result.stdout.split()[2] != '500' or gf_result.stdout.split()[3] != '500':
        gf.close_session()
        raise error.TestFail("lchown failed.")
    gf.close_session()


def test_du(vm, params):
    """
    Test du command
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
    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')
    gf_result = gf.du("/file_ops")
    if int(gf_result.stdout.split()[0]) > 13200 or int(gf_result.stdout.split()[0]) < 13000:
        gf.close_session()
        raise error.TestFail("du failed.")
    gf.close_session()


def test_file(vm, params):
    """
    Test du command
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
    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')
    file_check = {"/file_ops/file_ascii": "ASCII text",
                  "/file_ops/file_ascii_long": "ASCII text, with very long lines",
                  "/file_ops/file_elf": "ELF 64-bit LSB executable, x86-64",
                  "/file_ops/file_dev_block": 'block device',
                  "/file_ops/file_dev_fifo": 'FIFO',
                  "/file_ops/file_ascii_softlink": 'symbolic link',
                  "/file_ops/file_ascii_softlink_link": 'symbolic link',
                  "/file_ops/file_ascii_badlink": 'symbolic link',
                  "/file_ops/file_dev_char": 'character device'}

    for k, v in file_check.items():
        gf_result = gf.file(k).stdout.strip()
        if v not in gf_result:
            gf.close_session()
            raise error.TestFail("file failed.")

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

    for image_format in re.findall("\w+", image_formats):
        params["image_format"] = image_format
        for partition_type in re.findall("\w+", partition_types):
            params["partition_type"] = partition_type
            prepare_image(params)
            testcase(vm, params)
