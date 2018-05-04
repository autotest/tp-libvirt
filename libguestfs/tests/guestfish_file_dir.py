import logging
import os
import re
import random
import string
import hashlib

from avocado.utils import process

from virttest import utils_test
from virttest import data_dir


def prepare_image(test, params):
    """
    (1) Create a image
    (2) Create file system on the image
    """
    params["image_path"] = utils_test.libguestfs.preprocess_image(params)

    if not params.get("image_path"):
        test.fail("Image could not be created for some reason.")

    tarball_file = params.get("tarball_file")
    if tarball_file:
        tarball_path = os.path.join(data_dir.get_deps_dir(), "tarball",
                                    tarball_file)
    params["tarball_path"] = tarball_path

    gf = utils_test.libguestfs.GuestfishTools(params)
    status, output = gf.create_fs()
    if status is False:
        gf.close_session()
        test.fail(output)
    gf.close_session()


def test_chmod(test, vm, params):
    """
    Test chmod command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
        test.fail("chmod failed.")
    gf.chmod(str(ori_perm), "/file_ops/file_ascii")
    gf_result = gf.ll("/file_ops/file_ascii")
    if gf_result.stdout.split()[0] != ori_perm_oct:
        gf.close_session()
        test.fail("chmod failed.")
    if gf_result.exit_status:
        gf.close_session()
        test.fail("chmod failed.")
    logging.info("Get readonly status successfully.")
    gf.close_session()


def test_chown(test, vm, params):
    """
    Test chown command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
        test.fail("chown failed.")
    gf.chown(500, 500, "/file_ops/file_ascii")
    gf_result = gf.ll("/file_ops/file_ascii")
    if gf_result.stdout.split()[2] != '500' or gf_result.stdout.split()[3] != '500':
        gf.close_session()
        test.fail("chown failed.")
    gf.chown(100, 100, "/file_ops/file_ascii_softlink")
    gf_result = gf.ll("/file_ops/file_ascii_softlink")
    if gf_result.stdout.split()[2] != 'root' or gf_result.stdout.split()[3] != 'root':
        gf.close_session()
        test.fail("chown failed.")
    gf.chown(500, 500, "/file_ops/file_ascii_softlink")
    gf_result = gf.ll("/file_ops/file_ascii_softlink")
    if gf_result.stdout.split()[2] != 'root' or gf_result.stdout.split()[3] != 'root':
        gf.close_session()
        test.fail("chown failed.")
    gf.close_session()


def test_lchown(test, vm, params):
    """
    Test lchown command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
        test.fail("lchown failed.")
    gf.lchown(500, 500, "/file_ops/file_ascii")
    gf_result = gf.ll("/file_ops/file_ascii")
    if gf_result.stdout.split()[2] != '500' or gf_result.stdout.split()[3] != '500':
        gf.close_session()
        test.fail("lchown failed.")
    gf.lchown(100, 100, "/file_ops/file_ascii_softlink")
    gf_result = gf.ll("/file_ops/file_ascii_softlink")
    if gf_result.stdout.split()[2] != '100' or gf_result.stdout.split()[3] != 'users':
        gf.close_session()
        test.fail("lchown failed.")
    gf.lchown(500, 500, "/file_ops/file_ascii_softlink")
    gf_result = gf.ll("/file_ops/file_ascii_softlink")
    if gf_result.stdout.split()[2] != '500' or gf_result.stdout.split()[3] != '500':
        gf.close_session()
        test.fail("lchown failed.")
    gf.close_session()


def test_du(test, vm, params):
    """
    Test du command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
        test.fail("du failed.")
    gf.close_session()


def test_file(test, vm, params):
    """
    Test file command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    for k, v in list(file_check.items()):
        gf_result = gf.file(k).stdout.strip()
        if v not in gf_result:
            gf.close_session()
            test.fail("file failed.")

    gf.close_session()


def test_file_architecture(test, vm, params):
    """
    Test file-architecture command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
    file_architecture_check = {"/file_ops/file_x64": "x86_64",
                               "/file_ops/file_i386": "i386",
                               "/file_ops/file_ia64": "ia64",
                               "/file_ops/file_ppc": 'ppc',
                               "/file_ops/file_ppc64": 'ppc64',
                               "/file_ops/file_sparc": 'sparc'}

    for k, v in list(file_architecture_check.items()):
        gf_result = gf.file_architecture(k).stdout.strip()
        if v.upper() not in gf_result.upper():
            gf.close_session()
            test.fail("file-architecture failed.")

    gf.close_session()


def test_filesize(test, vm, params):
    """
    Test filesize command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
    filesize_check = {"/file_ops/file_ascii": "653",
                      "/file_ops/file_ascii_long": "5400593",
                      "/file_ops/file_elf": "927256",
                      "/file_ops/file_dev_block": '0',
                      "/file_ops/file_dev_fifo": '0',
                      "/file_ops/file_ascii_softlink": '653',
                      "/file_ops/file_ascii_softlink_link": '653',
                      "/file_ops/file_dev_char": '0'}

    for k, v in list(filesize_check.items()):
        gf_result = gf.filesize(k).stdout.strip()
        if v not in gf_result:
            gf.close_session()
            test.fail("filesize failed.")

    gf.close_session()


def test_stat(test, vm, params):
    """
    Test stat command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
    stat_check = {"/file_ops/file_ascii": "653",
                  "/file_ops/file_ascii_long": "5400593",
                  "/file_ops/file_elf": "927256",
                  "/file_ops/file_dev_block": '0',
                  "/file_ops/file_dev_fifo": '0',
                  "/file_ops/file_ascii_softlink": '653',
                  "/file_ops/file_ascii_softlink_link": '653',
                  "/file_ops/file_dev_char": '0'}

    for k, v in list(stat_check.items()):
        gf_result = gf.stat(k).stdout.strip()
        if v not in gf_result:
            gf.close_session()
            test.fail("stat failed.")

    gf.close_session()


def test_lstat(test, vm, params):
    """
    Test lstat command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
    lstat_check = {"/file_ops/file_ascii": "653",
                   "/file_ops/file_ascii_long": "5400593",
                   "/file_ops/file_elf": "927256",
                   "/file_ops/file_dev_block": '0',
                   "/file_ops/file_dev_fifo": '0',
                   "/file_ops/file_ascii_softlink": '10',
                   "/file_ops/file_ascii_softlink_link": '19',
                   "/file_ops/file_dev_char": '0'}

    for k, v in list(lstat_check.items()):
        gf_result = gf.lstat(k).stdout.strip()
        if v not in gf_result:
            gf.close_session()
            test.fail("lstat failed.")

    gf.close_session()


def test_lstatlist(test, vm, params):
    """
    Test lstatlist command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
    lstat_check = {"/file_ops/file_ascii": "653",
                   "/file_ops/file_ascii_long": "5400593",
                   "/file_ops/file_elf": "927256",
                   "/file_ops/file_dev_block": '0',
                   "/file_ops/file_dev_fifo": '0',
                   "/file_ops/file_ascii_softlink": '10',
                   "/file_ops/file_ascii_softlink_link": '19',
                   "/file_ops/file_dev_char": '0'}

    gf_result = gf.lstatlist("/file_ops", "\"file_ascii file_ascii_long file_elf file_dev_block file_dev_fifo file_ascii_softlink file_ascii_softlink_link file_dev_char\"").stdout.strip()
    for k, v in list(lstat_check.items()):
        if v not in gf_result:
            gf.close_session()
            test.fail("lstatlist failed.")

    gf.close_session()


def test_touch(test, vm, params):
    """
    Test touch command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
    touch_check = {"/file_ops/file_ascii": "0",
                   "/file_ops/file_ascii_long": "0",
                   "/file_ops/file_elf": "0",
                   "/file_ops/file_dev_block": '1',
                   "/file_ops/file_dev_fifo": '1',
                   "/file_ops/file_ascii_softlink": '1',
                   "/file_ops/file_ascii_softlink_link": '1',
                   "/file_ops/file_dev_char": '1'}

    for k, v in list(touch_check.items()):
        gf_result = gf.touch(k).exit_status
        if int(v) != gf_result:
            gf.close_session()
            test.fail("touch failed.")

    gf.close_session()


def test_umask(test, vm, params):
    """
    Test umask command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
    gf_result = gf.umask("022")
    gf_result = gf.get_umask()
    if gf_result.stdout.strip() != "022":
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.touch("/file_ops/umask0022")
    gf_result = gf.ll("/file_ops/umask0022")
    if gf_result.stdout.split()[0] != "-rw-r--r--":
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.rm("/file_ops/umask0022")

    gf_result = gf.umask("0")
    gf_result = gf.get_umask()
    if gf_result.stdout.strip() != "0":
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.touch("/file_ops/umask0000")
    gf_result = gf.ll("/file_ops/umask0000")
    if gf_result.stdout.split()[0] != "-rw-rw-rw-":
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.rm("/file_ops/umask0000")

    gf_result = gf.umask("066")
    gf_result = gf.get_umask()
    if gf_result.stdout.strip() != "066":
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.touch("/file_ops/umask0066")
    gf_result = gf.ll("/file_ops/umask0066")
    if gf_result.stdout.split()[0] != "-rw-------":
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.rm("/file_ops/umask0066")

# regress bug 627832
    gf_result = gf.umask("022")
    gf_result = gf.get_umask()
    if gf_result.stdout.strip() != "022":
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.umask("-1")
    if gf_result.exit_status != 1:
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.get_umask()
    if gf_result.stdout.strip() != "022":
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.umask("01000")
    if gf_result.exit_status != 1:
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.get_umask()
    if gf_result.stdout.strip() != "022":
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.touch("/testfoo")
    gf_result = gf.chmod("-1", "/testfoo")
    if gf_result.exit_status != 1:
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.mkdir_mode("/testdoo", "-1")
    if gf_result.exit_status != 1:
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.mknod("-1", "8", "1", "/testnoo")
    if gf_result.exit_status != 1:
        gf.close_session()
        test.fail("umask failed.")
    gf_result = gf.rm_rf("/testfoo")
    gf_result = gf.rm_rf("/testdoo")
    gf_result = gf.rm_rf("/testnoo")

    gf.close_session()


def test_cat(test, vm, params):
    """
    Test cat command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    gf_result = gf.cat('/file_ops/file_ascii').stdout.strip()
    logging.debug(gf_result)
    run_result = process.run("tar xOf %s file_ops/file_ascii" % params.get("tarball_path"),
                             ignore_status=True, shell=True).stdout_text.strip()
    logging.debug(run_result)
    if gf_result != run_result:
        gf.close_session()
        test.fail("cat failed.")

    gf.close_session()


def test_checksum(test, vm, params):
    """
    Test checksum command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
    checksum_map = {"crc": "cksum",
                    "md5": "md5sum",
                    "sha1": "sha1sum",
                    "sha224": 'sha224sum',
                    "sha256": 'sha256sum',
                    "sha384": 'sha384sum',
                    "sha512": 'sha512sum'}

    for k, v in list(checksum_map.items()):
        gf_result = gf.checksum(k, '/file_ops/file_ascii').stdout.strip()
        run_result = process.run("tar xOf %s file_ops/file_ascii | %s" % (params.get("tarball_path"), v),
                                 ignore_status=True, shell=True).stdout_text.split()[0]
        if gf_result != run_result:
            gf.close_session()
            test.fail("checksum failed.")

    for k, v in list(checksum_map.items()):
        gf_result = gf.checksum(k, '/file_ops/file_elf').stdout.strip()
        run_result = process.run("tar xOf %s file_ops/file_elf | %s" % (params.get("tarball_path"), v),
                                 ignore_status=True, shell=True).stdout_text.split()[0]
        if gf_result != run_result:
            gf.close_session()
            test.fail("checksum failed.")

    gf_result = gf.checksum('perfect', '/file_ops/file_elf')
    logging.debug(gf_result.stdout.strip())
    if gf_result.exit_status == 0:
        gf.close_session()
        test.fail("checksum failed.")

    gf.close_session()


def test_checksum_device(test, vm, params):
    """
    Test checksum-device command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()
    checksum_map = {"crc": "cksum",
                    "md5": "md5sum",
                    "sha1": "sha1sum",
                    "sha224": 'sha224sum',
                    "sha256": 'sha256sum',
                    "sha384": 'sha384sum',
                    "sha512": 'sha512sum'}

    for k, v in list(checksum_map.items()):
        gf_result = gf.checksum_device(k, '/dev/sda').stdout.strip()
        logging.debug(gf_result.split('\n')[-1])
        run_result = process.run("%s %s" % (v, params.get("image_path")),
                                 ignore_status=True, shell=True).stdout_text.split()[0]
        logging.debug(run_result)
        if gf_result.split('\n')[-1] != run_result and params.get("image_format") == 'raw':
            gf.close_session()
            test.fail("checksum failed.")

    gf_result = gf.checksum_device('perfect', '/dev/sda')
    logging.debug(gf_result.stdout.strip())
    if gf_result.exit_status == 0:
        gf.close_session()
        test.fail("checksum-device failed.")

    gf.close_session()


def test_checksums_out(test, vm, params):
    """
    Test checksums-out command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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
    checksum_map = {"crc": "cksum",
                    "md5": "md5sum",
                    "sha1": "sha1sum",
                    "sha224": 'sha224sum',
                    "sha256": 'sha256sum',
                    "sha384": 'sha384sum',
                    "sha512": 'sha512sum'}

    gf.rm_rf('/test/')
    gf.mkdir('/test')
    gf.cp_a('/file_ops/file_ascii', '/test')
    gf.cp_a('/file_ops/file_ascii_softlink', '/test')
    gf.mkdir('/test/subdir')
    gf.cp_a('/file_ops/file_elf', '/test/subdir/')
    gf.download('/test/subdir/file_elf', '/tmp/file_elf')
    gf.download('/test/file_ascii', '/tmp/file_ascii')
    for k, v in list(checksum_map.items()):
        gf_result = gf.checksums_out(k, '/test', '/tmp/sumsfile').stdout.strip()
        run_result = process.run("cat /tmp/sumsfile", shell=True).stdout_text.split()
        if k == 'crc':
            run_result[2] = os.path.basename(run_result[2])
            run_result[5] = os.path.basename(run_result[5])
            guest_res = dict(list(zip(run_result[2::3], run_result[0::3])))
            run_result = process.run("%s /tmp/file_elf /tmp/file_ascii" % v,
                                     ignore_status=True, shell=True).stdout_text.split()
            run_result[2] = os.path.basename(run_result[2])
            run_result[5] = os.path.basename(run_result[5])
            host_res = dict(list(zip(run_result[2::3], run_result[0::3])))
        else:
            run_result[1] = os.path.basename(run_result[1])
            run_result[3] = os.path.basename(run_result[3])
            guest_res = dict(list(zip(run_result[1::2], run_result[0::2])))
            run_result = process.run("%s /tmp/file_elf /tmp/file_ascii" % v,
                                     ignore_status=True, shell=True).stdout_text.split()
            run_result[1] = os.path.basename(run_result[1])
            run_result[3] = os.path.basename(run_result[3])
            host_res = dict(list(zip(run_result[1::2], run_result[0::2])))
        if guest_res != host_res:
            gf.close_session()
            test.fail("checksum failed.")

    gf_result = gf.checksums_out('perfect', '/test', '/tmp/sumsfile')
    logging.debug(gf_result.stdout.strip())
    if gf_result.exit_status == 0:
        gf.close_session()
        test.fail("checksums-out failed.")
    gf.rm_rf('/test')

    gf.close_session()


def test_equal(test, vm, params):
    """
    Test equal command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    gf_result = gf.equal('/file_ops/file_ascii', '/file_ops/file_ascii_long').stdout.strip()
    if gf_result != 'false':
        gf.close_session()
        test.fail("equal failed.")

    gf_result = gf.equal('/file_ops/file_ascii', '/file_ops/file_elf').stdout.strip()
    if gf_result != 'false':
        gf.close_session()
        test.fail("equal failed.")

    gf_result = gf.equal('/file_ops/file_ascii', '/file_ops/file_ascii_softlink').stdout.strip()
    if gf_result != 'true':
        gf.close_session()
        test.fail("equal failed.")

    gf.close_session()


def test_fill(test, vm, params):
    """
    Test fill command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    gf_result = gf.fill(45, 1000, '/newfile')
    gf_result = gf.download('/newfile', '/tmp/newfile')
    gf_result = gf.rm('/newfile')

    run_result = process.run("for((i=0;i<100;i++)); do echo -n '----------'; done > /tmp/tmp", shell=True).stdout_text.split()
    run_result = process.run("cmp /tmp/newfile /tmp/tmp", shell=True).stdout_text.strip()
    if run_result != '':
        gf.close_session()
        test.fail("fill failed.")

    gf_result = gf.fill('066', 10000000, '/newtest')
    gf_result = gf.strings('/newtest').stdout.strip()
    if 'maybe the reply exceeds the maximum message size in the protocol?' not in gf_result:
        gf.close_session()
        test.fail("fill failed.")

    gf_result = gf.head('/newtest').stdout.strip()
    if 'maybe the reply exceeds the maximum message size in the protocol?' not in gf_result:
        gf.close_session()
        test.fail("fill failed.")

    gf_result = gf.head_n(100000, '/newtest').stdout.strip()
    if 'maybe the reply exceeds the maximum message size in the protocol?' not in gf_result:
        gf.close_session()
        test.fail("fill failed.")

    gf_result = gf.tail('/newtest').stdout.strip()
    if 'maybe the reply exceeds the maximum message size in the protocol?' not in gf_result:
        gf.close_session()
        test.fail("fill failed.")

    gf_result = gf.pread('/newtest', 10000000, 0).stdout.strip()
    if 'count is too large for the protocol' not in gf_result:
        gf.close_session()
        test.fail("fill failed.")

    gf.close_session()


def test_fill_dir(test, vm, params):
    """
    Test fill-dir command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    fill_num = random.randint(0, 99)
    gf_result = gf.mkdir('/fill/')
    gf_result = gf.fill_dir('/fill/', fill_num)
    gf_result = gf.ls('/fill/').stdout.strip()

    cmp_result = ''
    for i in range(fill_num):
        cmp_result += string.zfill(i, 8)
        cmp_result += '\n'
    cmp_result = cmp_result[0:len(cmp_result)-1]

    if gf_result != cmp_result:
        gf.close_session()
        test.fail("fill failed.")

    gf.close_session()


def test_fill_pattern(test, vm, params):
    """
    Test fill-pattern command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    gf_result = gf.fill_pattern('abcdefgh', 1000, '/newfile')
    gf_result = gf.download('/newfile', '/tmp/newfile')
    gf_result = gf.rm('/newfile')

    run_result = process.run("for((i=0;i<125;i++)); do echo -n 'abcdefgh'; done > /tmp/tmp", shell=True).stdout_text.split()
    run_result = process.run("cmp /tmp/newfile /tmp/tmp", shell=True).stdout_text.strip()
    if run_result != '':
        gf.close_session()
        test.fail("fill-pattern failed.")

    gf_result = gf.fill_pattern('abcdef', 10000000, '/newtest')
    gf_result = gf.hexdump('/newtest').stdout.strip()
    if 'maybe the reply exceeds the maximum message size in the protocol?' not in gf_result:
        gf.close_session()
        test.fail("fill-pattern failed.")

    gf_result = gf.strings('/newtest').stdout.strip()
    if 'maybe the reply exceeds the maximum message size in the protocol?' not in gf_result:
        gf.close_session()
        test.fail("fill-pattern failed.")

    gf_result = gf.head('/newtest').stdout.strip()
    if 'maybe the reply exceeds the maximum message size in the protocol?' not in gf_result:
        gf.close_session()
        test.fail("fill-pattern failed.")

    gf_result = gf.head_n(100000, '/newtest').stdout.strip()
    if 'maybe the reply exceeds the maximum message size in the protocol?' not in gf_result:
        gf.close_session()
        test.fail("fill-pattern failed.")

    gf_result = gf.tail('/newtest').stdout.strip()
    if 'maybe the reply exceeds the maximum message size in the protocol?' not in gf_result:
        gf.close_session()
        test.fail("fill-pattern failed.")

    gf_result = gf.pread('/newtest', 10000000, 0).stdout.strip()
    if 'count is too large for the protocol' not in gf_result:
        gf.close_session()
        test.fail("fill-pattern failed.")

    gf.close_session()


def test_head(test, vm, params):
    """
    Test head command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    gf_result = gf.head('/file_ops/file_ascii').stdout.strip()
    if len(gf_result) != 253:
        gf.close_session()
        test.fail("head failed.")
    gf_result = gf.head_n(3, '/file_ops/file_ascii').stdout.strip()
    if len(gf_result) != 73:
        gf.close_session()
        test.fail("head failed.")
    gf_result = gf.head_n(1000, '/file_ops/file_ascii').stdout.strip()
    if len(gf_result) != 652:
        gf.close_session()
        test.fail("head failed.")
    gf_result = gf.head_n(0, '/file_ops/file_ascii').exit_status
    if gf_result != 0:
        gf.close_session()
        test.fail("head failed.")
    gf_result = gf.head_n('+5', '/file_ops/file_ascii').exit_status
    if gf_result != 0:
        gf.close_session()
        test.fail("head failed.")
    gf_result = gf.head_n('-5', '/file_ops/file_ascii').exit_status
    if gf_result != 0:
        gf.close_session()
        test.fail("head failed.")

    gf.close_session()


def test_hexdump(test, vm, params):
    """
    Test hexdump command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    gf_result = gf.hexdump('/file_ops/file_ascii').stdout.strip()
    m = hashlib.md5()
    m.update(gf_result)
    logging.debug(m.hexdigest())
    if m.hexdigest() != '3ca9739a70e246745ee6bb55e11f755b':
        gf.close_session()
        test.fail("hexdump failed.")
    gf_result = gf.hexdump('/file_ops/file_tgz').stdout.strip()
    m = hashlib.md5()
    m.update(gf_result)
    logging.debug(m.hexdigest())
    if m.hexdigest() != 'ee00d7203bea3081453c4f41e29f92b4':
        gf.close_session()
        test.fail("hexdump failed.")

    gf.close_session()


def test_more(test, vm, params):
    """
    Test more command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    gf_result = gf.more('/file_ops/file_ascii').stdout.strip()
    if len(gf_result) != 652:
        gf.close_session()
        test.fail("more failed.")

    gf.close_session()


def test_pread(test, vm, params):
    """
    Test pread command
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

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

    # inner cmd will delete the last none empty line
    gf_result = gf.pread('/file_ops/file_ascii', 11, 80).stdout
    if len(gf_result) != 11:
        gf.close_session()
        test.fail("pread failed.")

    gf_result = gf.pread('/file_ops/file_ascii', 1000, 0).stdout.strip()
    if len(gf_result) != 652:
        gf.close_session()
        test.fail("pread failed.")

    gf.close_session()


def test_manually_(test, vm, params):
    """
    This API will be tested manually
    """
    pass


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
            prepare_image(test, params)
            testcase(test, vm, params)
