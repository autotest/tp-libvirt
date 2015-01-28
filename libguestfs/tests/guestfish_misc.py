from autotest.client.shared import error, utils
from virttest import utils_test, utils_misc, data_dir
from virttest.tests import unattended_install
import commands
import logging
import shutil
import os
import re
import time


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


def test_df(vm, params):
    """
    Test command df
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
    gf.do_mount("/")
    result = gf.df().stdout.strip()

    gf.close_session()

    physical_name = "/dev/sda"
    lvm_name = "/dev/mapper/vol_test-vol_file"

    if physical_name not in result and lvm_name not in result:
        raise error.TestFail("Could not find df output")


def test_df_h(vm, params):
    """
    Test command df-h
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
    gf.do_mount("/")
    result = gf.df_h().stdout.strip()

    gf.close_session()

    physical_name = "/dev/sda"
    lvm_name = "/dev/mapper/vol_test-vol_file"

    if physical_name not in result and lvm_name not in result:
        raise error.TestFail("Could not find df output")

    if not re.search("\d+[MKG]", result):
        raise error.TestFail("It's not a human-readable format")


def test_dd(vm, params):
    """
    Test command dd
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
    gf.do_mount("/")

    gf.write("/src.txt", "Hello World")
    gf.dd("/src.txt", "/dest.txt")

    src_size = gf.filesize("/src.txt").stdout.strip()
    dest_size = gf.filesize("/dest.txt").stdout.strip()
    dest_content = gf.cat("/dest.txt").stdout.strip()

    gf.close_session()

    if src_size != dest_size or dest_content != "Hello World":
        raise error.TestFail("Content or filesize is not match")


def test_copy_size(vm, params):
    """
    Test command copy-size
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
    gf.do_mount("/")

    gf.write("/src.txt", "Hello World")
    src_size = gf.filesize("/src.txt").stdout.strip()

    gf.copy_size("/src.txt", "/dest.txt", src_size)
    dest_size = gf.filesize("/dest.txt").stdout.strip()
    dest_content = gf.cat("/dest.txt").stdout.strip()

    gf.close_session()

    if src_size != dest_size or dest_content != "Hello World":
        raise error.TestFail("Content or filesize is not match")


def test_download(vm, params):
    """
    Test command download
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
    gf.do_mount("/")

    gf.write("/src.txt", "Hello World")
    src_size = gf.filesize("/src.txt").stdout.strip()

    dest = "%s/dest.txt" % data_dir.get_tmp_dir()
    gf.download("/src.txt", "%s" % dest)
    gf.close_session()

    content = commands.getoutput("cat %s" % dest)
    commands.getstatus("rm %s" % dest)

    if content != "Hello World":
        raise error.TestFail("Content or filesize is not match")


def test_download_offset(vm, params):
    """
    Test command download-offset
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
    gf.do_mount("/")

    string = "Hello World"

    gf.write("/src.txt", string)
    src_size = gf.filesize("/src.txt").stdout.strip()

    dest = "%s/dest.txt" % data_dir.get_tmp_dir()
    gf.download_offset("/src.txt", "%s" % dest, 0, len(string))
    gf.close_session()

    content = commands.getoutput("cat %s" % dest)
    commands.getstatus("rm %s" % dest)

    if content != "Hello World":
        raise error.TestFail("Content or filesize is not match")


def test_upload(vm, params):
    """
    Test command upload
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
    gf.do_mount("/")

    filename = "%s/src.txt" % data_dir.get_tmp_dir()
    fd = open(filename, "w+")
    fd.write("Hello World")
    fd.close()

    gf.upload(filename, "/dest.txt")
    content = gf.cat("/dest.txt").stdout.strip()
    gf.close_session()
    commands.getoutput("rm %s" % filename)

    if content != "Hello World":
        raise error.TestFail("Content is not correct")


def test_upload_offset(vm, params):
    """
    Test command upload_offset
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
    gf.do_mount("/")

    filename = "%s/src.txt" % data_dir.get_tmp_dir()
    string = "Hello World"
    commands.getoutput("echo %s > %s" % (string, filename))

    gf.upload_offset(filename, "/dest.txt", 0)
    content = gf.cat("/dest.txt").stdout.strip()
    gf.close_session()
    commands.getoutput("rm %s" % filename)

    if content != string:
        raise error.TestFail("Content is not correct")


def test_fallocate(vm, params):
    """
    Test command fallocate
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
    gf.do_mount("/")

    # preallocates a new file
    gf.fallocate("/new", 100)
    size = gf.filesize("/new").stdout.strip()

    if int(size) != 100:
        gf.close_session()
        raise error.TestFail("File size is not correct")

    # overwriteen a existed file
    string = "Hello World"
    gf.write("/exist", "Hello world")
    gf.fallocate("/exist", 200)
    size = gf.filesize("/exist").stdout.strip()
    content = gf.cat("/exist").stdout.strip()
    gf.close_session()

    if content != "":
        raise error.TestFail("Content is not empty")
    if int(size) != 200:
        raise error.TestFail("File size is not correct")


def test_fallocate64(vm, params):
    """
    Test command fallocate64
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
    gf.do_mount("/")

    # preallocates a new file
    gf.fallocate("/new", 100)
    size = gf.filesize("/new").stdout.strip()

    if int(size) != 100:
        gf.close_session()
        raise error.TestFail("File size is not correct")

    # overwriteen a existed file
    string = "Hello World"
    gf.write("/exist", "Hello world")
    gf.fallocate64("/exist", 200)
    size = gf.filesize("/exist").stdout.strip()
    content = gf.cat("/exist").stdout.strip()
    gf.close_session()

    if content != "":
        raise error.TestFail("Content is not empty")
    if int(size) != 200:
        raise error.TestFail("File size is not correct")


def test_drop_caches(vm, params):
    """
    Test command drop_cashes
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = params.get("gf_add_readonly", "no")

    logging.debug("@@@@@@@@@@@@@@@@@@@@@@@")
    logging.debug(params.get("image_path"))
    gf = utils_test.libguestfs.GuestfishTools(params)

    if add_ref == "disk":
        image_path = params.get("image_path")
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    gf.run()

    # Drop pagecache, dentries and inodes
    cache_option = 3
    drop_caches_result = gf.drop_caches(cache_option)
    logging.debug(drop_caches_result)

    gf.close_session()

    if drop_caches_result.exit_status:
        raise error.TestFail("Drop pagecache, dentries and inodes failed")
    else:
        logging.info("Drop pagecache, dentries and inodes successfully")


def test_case_sensitive_path(vm, params):
    """
    Test command case-sensitive-path
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
    gf.do_mount("/")

    # get the true path on case-insensitive filesystem
    gf.write("/src.txt", "Hello World")
    tmp_path = "/sRC.TXT"
    case_sensitive_path_result = gf.case_sensitive_path(tmp_path)
    logging.debug(case_sensitive_path_result)

    gf.close_session()

    if case_sensitive_path_result.exit_status or not \
       case_sensitive_path_result.stdout.strip() == "/src.txt":
        raise error.TestFail("failed to execute 'case_sensitive_path'")
    else:
        logging.info("execute 'case_sensitive_path' successfully")


def test_command(vm, params):
    """
    Test command command
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

    # mount the partition with os
    inspectos_result = gf.inspect_os()
    logging.debug(inspectos_result)
    roots_list = inspectos_result.stdout.splitlines()
    if inspectos_result.exit_status or not len(roots_list):
        gf.close_session()
        raise error.TestFail("Get os failed:%s" % inspectos_result)
    mount_result = gf.mount(roots_list[0], "/")
    logging.debug(mount_result)

    # run command "cat /src" from the guest filesystem
    tmpfile = "/src"
    logging.debug(gf.write(tmpfile, r"Hello\nWorld"))
    cmd_on_guest = ("cat %s" % tmpfile)

    command_result = gf.command(cmd_on_guest)
    logging.debug(command_result)

    gf.close_session()

    if command_result.exit_status or not \
       command_result.stdout.strip() == "Hello\nWorld":
        raise error.TestFail("failed to run command 'cat file'")
    else:
        logging.info("run command 'cat file' successfully")


def test_command_lines(vm, params):
    """
    Test command command-lines
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

    # mount the partition with os
    inspectos_result = gf.inspect_os()
    logging.debug(inspectos_result)
    roots_list = inspectos_result.stdout.splitlines()
    if inspectos_result.exit_status or not len(roots_list):
        gf.close_session()
        raise error.TestFail("Get os failed:%s" % inspectos_result)
    mount_result = gf.mount(roots_list[0], "/")
    logging.debug(mount_result)

    # run command-lines "cat /src" from the guest filesystem
    tmpfile = "/src"
    logging.debug(gf.write(tmpfile, r"Hello\nWorld"))
    cmd_on_guest = ("cat %s" % tmpfile)

    command_lines_result = gf.command_lines(cmd_on_guest)
    logging.debug(command_lines_result)

    gf.close_session()

    if command_lines_result.exit_status or not \
       command_lines_result.stdout.strip() == "Hello\nWorld":
        raise error.TestFail("failed to run command-lines 'cat file'")
    else:
        logging.info("run command-lines 'cat file' successfully")


def test_sh(vm, params):
    """
    Test command sh
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

    # mount the partition with os
    inspectos_result = gf.inspect_os()
    logging.debug(inspectos_result)
    roots_list = inspectos_result.stdout.splitlines()
    if inspectos_result.exit_status or not len(roots_list):
        gf.close_session()
        raise error.TestFail("Get os failed:%s" % inspectos_result)
    mount_result = gf.mount(roots_list[0], "/")
    logging.debug(mount_result)

    # run command-lines "cat /src" from the guest filesystem
    tmpfile = "/src"
    logging.debug(gf.write(tmpfile, r"Hello\nWorld"))
    cmd_on_guest = ("cat %s" % tmpfile)
    sh_result = gf.sh(cmd_on_guest)
    logging.debug(sh_result)

    gf.close_session()

    if sh_result.exit_status or not \
       sh_result.stdout.strip() == "Hello\nWorld":
        raise error.TestFail("failed to run sh 'cat file'")
    else:
        logging.info("run sh 'cat file' successfully")


def test_sh_lines(vm, params):
    """
    Test command sh-lines
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

    # mount the partition with os
    inspectos_result = gf.inspect_os()
    logging.debug(inspectos_result)
    roots_list = inspectos_result.stdout.splitlines()
    if inspectos_result.exit_status or not len(roots_list):
        gf.close_session()
        raise error.TestFail("Get os failed:%s" % inspectos_result)
    mount_result = gf.mount(roots_list[0], "/")
    logging.debug(mount_result)

    # run command-lines "cat /src" from the guest filesystem
    tmpfile = "/src"
    logging.debug(gf.write(tmpfile, r"Hello\nWorld"))
    cmd_on_guest = ("cat %s" % tmpfile)
    sh_lines_result = gf.sh_lines(cmd_on_guest)
    logging.debug(sh_lines_result)

    gf.close_session

    if sh_lines_result.exit_status or not \
       sh_lines_result.stdout.strip() == "Hello\nWorld":
        raise error.TestFail("failed to run sh-lines 'cat file'")
    else:
        logging.info("run sh-lines 'cat file' successfully")


def test_zero(vm, params):
    """
    Test command zero
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
    gf.do_mount("/")

    # zero the mounted partition
    mounts_result = gf.mounts()
    logging.debug(mounts_result)
    mounted_patition_result = mounts_result.stdout.strip()
    if mounts_result.exit_status or not len(mounted_patition_result):
        gf.close_session()
        raise error.TestFail("Get partitions failed:%s" % mounted_patition_result)
    zero_result = gf.zero(mounted_patition_result)
    logging.debug(zero_result)
    file_result = gf.file(mounted_patition_result)
    logging.debug(file_result)

    gf.close_session()

    if zero_result.exit_status or not file_result.stdout.strip() == "data":
        raise error.TestFail("failed to write zeroes to the device")
    else:
        logging.info("write zeroes to the device successfully")


def test_zero_device(vm, params):
    """
    Test command zero_device
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
    gf.do_mount("/")

    # zero the mounted partition
    mounts_result = gf.mounts()
    logging.debug(mounts_result)
    mounted_patition_result = mounts_result.stdout.strip()
    if mounts_result.exit_status or not len(mounted_patition_result):
        gf.close_session()
        raise error.TestFail("Get partitions failed:%s" % mounted_patition_result)
    zero_device_result = gf.zero_device(mounted_patition_result)
    logging.debug(zero_device_result)
    file_result = gf.file(mounted_patition_result)
    logging.debug(file_result)

    gf.close_session()

    if zero_device_result.exit_status or not \
       file_result.stdout.strip() == "data":
        raise error.TestFail("failed to write zeroes to the device")
    else:
        logging.info("write zeroes to the device successfully")


def test_is_zero_device(vm, params):
    """
    Test command is_zero_device
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
    gf.do_mount("/")

    # get the mounted partition
    mounts_result = gf.mounts()
    logging.debug(mounts_result)
    mounted_patition_result = mounts_result.stdout.strip()
    if mounts_result.exit_status or not len(mounted_patition_result):
        gf.close_session()
        raise error.TestFail("Get partitions failed:%s" % mounted_patition_result)

    is_zero_device_result = gf.is_zero_device(mounted_patition_result)
    logging.debug(is_zero_device_result)

    if is_zero_device_result.exit_status or not \
       is_zero_device_result.stdout.strip() == "false":
        gf.close_session()
        raise error.TestFail("failed to check the non-zero device")
    else:
        logging.info("Check the non-zero device successfully")

    # zero the mounted partition
    zero_device_result = gf.zero_device(mounted_patition_result)
    logging.debug(zero_device_result)
    is_zero_device_result = gf.is_zero_device(mounted_patition_result)
    logging.debug(is_zero_device_result)

    if is_zero_device_result.exit_status or not \
       is_zero_device_result.stdout.strip() == "true":
        gf.close_session()
        raise error.TestFail("failed to check the zero device")
    else:
        logging.info("Check the zero device successfully")

    gf.close_session()


def test_is_whole_device(vm, params):
    """
    Test command is_whole_device
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
    gf.do_mount("/")

    # get the mounted partition
    mounts_result = gf.mounts()
    logging.debug(mounts_result)
    mounted_patition_result = mounts_result.stdout.strip()
    if mounts_result.exit_status or not len(mounted_patition_result):
        gf.close_session()
        raise error.TestFail("Get partitions failed:%s" % mounted_patition_result)
    is_whole_device_result = gf.is_whole_device(mounted_patition_result)
    logging.debug(is_whole_device_result)

    if is_whole_device_result.exit_status or not \
       is_whole_device_result.stdout.strip() == "false":
        gf.close_session()
        raise error.TestFail("failed to check the non-whole device")
    else:
        logging.info("Check the non-whole device successfully")

    # get the first whole device
    list_devices_result = gf.list_devices()
    devices_result = list_devices_result.stdout.splitlines()
    if list_devices_result.exit_status or not len(devices_result):
        gf.close_session()
        raise error.TestFail("Get devices failed:%s" % devices_result)
    is_whole_device_result = gf.is_whole_device(devices_result[0])
    logging.debug(is_whole_device_result)

    if is_whole_device_result.exit_status or not \
       is_whole_device_result.stdout.strip() == "true":
        gf.close_session()
        raise error.TestFail("failed to check the whole device")
    else:
        logging.info("Check the whole device successfully")

    gf.close_session()


def test_is_zero(vm, params):
    """
    Test command is_zero
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
    gf.do_mount("/")

    # create a empty file
    tmpfile = "/src"
    gf.touch(tmpfile)
    is_zero_result = gf.is_zero(tmpfile)
    logging.debug(is_zero_result)

    if is_zero_result.exit_status or not \
       is_zero_result.stdout.strip() == "true":
        gf.close_session()
        raise error.TestFail("failed to check the empty file with is_zero")
    else:
        logging.info("Check the empty file with is_zero successfully")

    # write content to the file
    gf.write(tmpfile, "Hello World")
    is_zero_result = gf.is_zero(tmpfile)
    logging.debug(is_zero_result)

    if is_zero_result.exit_status or not \
       is_zero_result.stdout.strip() == "false":
        gf.close_session()
        raise error.TestFail("failed to check the non-empty file with is_zero")
    else:
        logging.info("Check the non-empty file with is_zero successfully")

    gf.close_session()


def test_grep(vm, params):
    """
    Test command grep
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
    gf.do_mount("/")

    # create file
    tmpfile = "/src"
    gf.write(tmpfile, r"Hello World\nhello world")
    grep_result = gf.grep("he.*\ w", tmpfile)
    logging.debug(grep_result)

    gf.close_session()

    if grep_result.exit_status or not \
       grep_result.stdout.strip() == "hello world":
        raise error.TestFail("fails to execute grep")
    else:
        logging.info("execute grep successfully")


def test_grepi(vm, params):
    """
    Test command grepi
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
    gf.do_mount("/")

    # create file
    tmpfile = "/src"
    gf.write(tmpfile, r"Hello World\nhello world")
    grepi_result = gf.grepi("he.*\ w", tmpfile)
    logging.debug(grepi_result)

    gf.close_session()

    grepi_lines = grepi_result.stdout.splitlines()
    if grepi_result.exit_status or not \
       grepi_lines[0] == "Hello World" \
       or not grepi_lines[1] == "hello world":
        raise error.TestFail("fails to execute grepi")
    else:
        logging.info("execute grepi successfully")


def test_fgrep(vm, params):
    """
    Test command fgrep
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
    gf.do_mount("/")

    # create file
    tmpfile = "/src"
    gf.write(tmpfile, r"Hello World\nhello.*.+ world")
    fgrep_result = gf.fgrep("o.*.+", tmpfile)
    logging.debug(fgrep_result)

    gf.close_session()

    if fgrep_result.exit_status or not \
       fgrep_result.stdout.strip() == "hello.*.+ world":
        raise error.TestFail("fails to execute fgrep")
    else:
        logging.info("execute fgrep successfully")


def test_fgrepi(vm, params):
    """
    Test command fgrepi
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
    gf.do_mount("/")

    # create file
    tmpfile = "/src"
    gf.write(tmpfile, r"Hello World\nhello.*.+ world")
    fgrepi_result = gf.fgrepi("O.*.+", tmpfile)
    logging.debug(fgrepi_result)

    gf.close_session()

    if fgrepi_result.exit_status or not \
       fgrepi_result.stdout.strip() == "hello.*.+ world":
        raise error.TestFail("fails to execute fgrepi")
    else:
        logging.info("execute fgrepi successfully")


def test_egrep(vm, params):
    """
    Test command egrep
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
    gf.do_mount("/")

    # create file
    tmpfile = "/src"
    gf.write(tmpfile, r"Hello World\nhello* world")
    egrep_result = gf.egrep("^h.+", tmpfile)
    logging.debug(egrep_result)

    gf.close_session()

    if egrep_result.exit_status or not \
       egrep_result.stdout.strip() == "hello* world":
        raise error.TestFail("fails to execute egrep")
    else:
        logging.info("execute egrep successfully")


def test_egrepi(vm, params):
    """
    Test command egrepi
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
    gf.do_mount("/")

    # create file
    tmpfile = "/src"
    gf.write(tmpfile, r"Hello World\nhello* world")
    egrepi_result = gf.egrepi("^h.+", tmpfile)
    logging.debug(egrepi_result)

    gf.close_session()

    egrepi_lines = egrepi_result.stdout.splitlines()
    if egrepi_result.exit_status or not \
       egrepi_lines[0] == "Hello World" \
       or not egrepi_lines[1] == "hello* world":
        raise error.TestFail("fails to execute egrepi")
    else:
        logging.info("execute egrepi successfully")


def test_zgrep(vm, params):
    """
    Test command zgrep
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
    gf.do_mount("/")

    # create gzip file
    tmpfile = "/src"
    tmpzfile = "/src.gz"
    gf.write(tmpfile, r"Hello World\nhello world")
    zfile_on_host = os.path.join(data_dir.get_tmp_dir(), "test_src.gz")
    gf.compress_out("gzip", tmpfile, zfile_on_host)
    gf.upload(zfile_on_host, tmpzfile)

    # remove the gzip file on host
    commands.getoutput("rm -rf %s" % zfile_on_host)

    zgrep_result = gf.zgrep("he.*\ w", tmpzfile)
    logging.debug(zgrep_result)

    gf.close_session()

    if zgrep_result.exit_status or not \
       zgrep_result.stdout.strip() == "hello world":
        raise error.TestFail("fails to execute zgrep")
    else:
        logging.info("execute zgrep successfully")


def test_zgrepi(vm, params):
    """
    Test command zgrepi
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
    gf.do_mount("/")

    # create gzip file
    tmpfile = "/src"
    tmpzfile = "/src.gz"
    gf.write(tmpfile, r"Hello World\nhello world")
    zfile_on_host = os.path.join(data_dir.get_tmp_dir(), "test_src.gz")
    gf.compress_out("gzip", tmpfile, zfile_on_host)
    gf.upload(zfile_on_host, tmpzfile)

    # remove the gzip file on host
    commands.getoutput("rm -rf %s" % zfile_on_host)

    zgrepi_result = gf.zgrepi("he.*\ w", tmpzfile)
    logging.debug(zgrepi_result)

    gf.close_session()

    zgrepi_lines = zgrepi_result.stdout.splitlines()
    if zgrepi_result.exit_status or not \
       zgrepi_lines[0] == "Hello World" \
       or not zgrepi_lines[1] == "hello world":
        raise error.TestFail("fails to execute zgrepi")
    else:
        logging.info("execute zgrepi successfully")


def test_zfgrep(vm, params):
    """
    Test command zfgrep
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
    gf.do_mount("/")

    # create gzip file
    tmpfile = "/src"
    tmpzfile = "/src.gz"
    gf.write(tmpfile, r"Hello World\nhello.*.+ world")
    zfile_on_host = os.path.join(data_dir.get_tmp_dir(), "test_src.gz")
    gf.compress_out("gzip", tmpfile, zfile_on_host)
    gf.upload(zfile_on_host, tmpzfile)

    # remove the gzip file on host
    commands.getoutput("rm -rf %s" % zfile_on_host)

    zfgrep_result = gf.zfgrep("o.*.+", tmpzfile)
    logging.debug(zfgrep_result)

    gf.close_session()

    if zfgrep_result.exit_status or not \
       zfgrep_result.stdout.strip() == "hello.*.+ world":
        raise error.TestFail("fails to execute zfgrep")
    else:
        logging.info("execute zfgrep successfully")


def test_zfgrepi(vm, params):
    """
    Test command zfgrepi
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
    gf.do_mount("/")

    # create gzip file
    tmpfile = "/src"
    tmpzfile = "/src.gz"
    gf.write(tmpfile, r"Hello World\nhello.*.+ world")
    zfile_on_host = os.path.join(data_dir.get_tmp_dir(), "test_src.gz")
    gf.compress_out("gzip", tmpfile, zfile_on_host)
    gf.upload(zfile_on_host, tmpzfile)

    # remove the gzip file on host
    commands.getoutput("rm -rf %s" % zfile_on_host)

    zfgrepi_result = gf.zfgrepi("O.*.+", tmpzfile)
    logging.debug(zfgrepi_result)

    gf.close_session()

    if zfgrepi_result.exit_status or not \
       zfgrepi_result.stdout.strip() == "hello.*.+ world":
        raise error.TestFail("fails to execute zfgrepi")
    else:
        logging.info("execute zfgrepi successfully")


def test_zegrep(vm, params):
    """
    Test command zegrep
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
    gf.do_mount("/")

    # create gzip file
    tmpfile = "/src"
    tmpzfile = "/src.gz"
    gf.write(tmpfile, r"Hello World\nhello* world")
    zfile_on_host = os.path.join(data_dir.get_tmp_dir(), "test_src.gz")
    gf.compress_out("gzip", tmpfile, zfile_on_host)
    gf.upload(zfile_on_host, tmpzfile)

    # remove the gzip file on host
    commands.getoutput("rm -rf %s" % zfile_on_host)

    zegrep_result = gf.zegrep("^h.+", tmpfile)
    logging.debug(zegrep_result)

    gf.close_session()

    if zegrep_result.exit_status or not \
       zegrep_result.stdout.strip() == "hello* world":
        raise error.TestFail("fails to execute zegrep")
    else:
        logging.info("execute zegrep successfully")


def test_zegrepi(vm, params):
    """
    Test command zegrepi
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
    gf.do_mount("/")

    # create gzip file
    tmpfile = "/src"
    tmpzfile = "/src.gz"
    gf.write(tmpfile, r"Hello World\nhello* world")
    zfile_on_host = os.path.join(data_dir.get_tmp_dir(), "test_src.gz")
    gf.compress_out("gzip", tmpfile, zfile_on_host)
    gf.upload(zfile_on_host, tmpzfile)

    # remove the gzip file on host
    commands.getoutput("rm -rf %s" % zfile_on_host)

    zegrepi_result = gf.zegrepi("^h.+", tmpfile)
    logging.debug(zegrepi_result)

    gf.close_session()

    zegrepi_lines = zegrepi_result.stdout.splitlines()
    if zegrepi_result.exit_status or not \
       zegrepi_lines[0] == "Hello World" \
       or not zegrepi_lines[1] == "hello* world":
        raise error.TestFail("fails to execute zegrepi")
    else:
        logging.info("execute zegrepi successfully")


def test_glob(vm, params):
    """
    Test command glob
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
    gf.do_mount("/")

    # create file
    tmpfile = "/glob_src"
    gf.write(tmpfile, r"Hello World")
    tmpfile_str = "/glob*"
    glob_result = gf.glob("cat", tmpfile_str)
    logging.debug(glob_result)

    gf.close_session()

    if glob_result.exit_status or not \
       glob_result.stdout.strip() == "Hello World":
        raise error.TestFail("fails to execute glob")
    else:
        logging.info("execute glob successfully")


def test_glob_expand(vm, params):
    """
    Test command glob-expand
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
    gf.do_mount("/")

    # create file
    tmpfile = "/glob_src"
    gf.write(tmpfile, r"Hello World")
    tmpfile_str = "/glob*"
    glob_expand_result = gf.glob_expand(tmpfile_str)
    logging.debug(glob_expand_result)

    if glob_expand_result.exit_status or \
       not glob_expand_result.stdout.strip() == tmpfile:
        gf.close_session()
        raise error.TestFail("fails to execute glob-expand")
    else:
        logging.info("execute glob-expand successfully")

    tmpfile_str = "/gold*"
    glob_expand_result = gf.glob_expand(tmpfile_str)

    if glob_expand_result.exit_status or not \
       glob_expand_result.stdout.strip() == "":
        gf.close_session()
        raise error.TestFail("fails to execute glob-expand")
    else:
        logging.info("execute glob-expand successfully")

    gf.close_session()


def test_mkmountpoint(vm, params):
    """
    Test command mkmountpoint
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

    # get the partition
    partition_type = params.get("partition_type")
    if partition_type == "lvm":
        vg_name = params.get("vg_name", "vol_test")
        lv_name = params.get("lv_name", "vol_file")
        device = "/dev/%s/%s" % (vg_name, lv_name)
        logging.info("mount lvm partition...%s" % device)
    elif partition_type == "physical":
        pv_name = params.get("pv_name", "/dev/sdb")
        device = pv_name + "1"
        logging.info("mount physical partition...%s" % device)

    tmp_mountpoint = "/tmpmount"
    mkmountpoint_result = gf.mkmountpoint(tmp_mountpoint)
    logging.debug(mkmountpoint_result)

    # test on the mountpoint
    gf.mount(device, tmp_mountpoint)
    touch_result = gf.touch("%s/test" % tmp_mountpoint)

    gf.close_session()

    if mkmountpoint_result.exit_status or touch_result.exit_status:
        raise error.TestFail("failed to make a mount point")
    else:
        logging.info("Make a mount point successfully")


def test_rmmountpoint(vm, params):
    """
    Test command rmmountpoint
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

    # get the partition
    partition_type = params.get("partition_type")
    if partition_type == "lvm":
        vg_name = params.get("vg_name", "vol_test")
        lv_name = params.get("lv_name", "vol_file")
        device = "/dev/%s/%s" % (vg_name, lv_name)
        logging.info("mount lvm partition...%s" % device)
    elif partition_type == "physical":
        pv_name = params.get("pv_name", "/dev/sdb")
        device = pv_name + "1"
        logging.info("mount physical partition...%s" % device)

    tmp_mountpoint = "/tmpmount"
    gf.mkmountpoint(tmp_mountpoint)
    rmmountpoint_result = gf.rmmountpoint(tmp_mountpoint)
    logging.debug(rmmountpoint_result)

    # test on the removed mountpoint
    mount_result = gf.mount(device, tmp_mountpoint)
    logging.debug(mount_result)

    gf.close_session()

    if rmmountpoint_result.exit_status or not mount_result.exit_status:
        raise error.TestFail("failed to remove a mount point")
    else:
        logging.info("Remove a mount point successfully")


def test_parse_environment(vm, params):
    """
    Test command parse-environment
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

    # set the trace flag as true
    tmp_var = "LIBGUESTFS_TRACE"
    logging.debug(gf.setenv(tmp_var, "1"))
    parse_enviroment_result = gf.parse_environment()
    logging.debug(parse_enviroment_result)

    get_trace_result = gf.get_trace().stdout.splitlines()
    logging.debug(get_trace_result[-1])
    if parse_enviroment_result.exit_status or not \
       get_trace_result[-1] == "true":
        gf.close_session()
        raise error.TestFail("failed to parse environment")
    else:
        logging.info("Parse environment successfully")

    # set the trace flag as false
    gf.setenv(tmp_var, "0")
    parse_enviroment_result = gf.parse_environment()
    logging.debug(parse_enviroment_result)

    get_trace_result = gf.get_trace().stdout.strip()
    logging.debug(get_trace_result)
    if parse_enviroment_result.exit_status or not \
       get_trace_result == "false":
        gf.close_session()
        raise error.TestFail("failed to parse environment")
    else:
        logging.info("Parse environment successfully")

    gf.close_session()


def test_parse_environment_list(vm, params):
    """
    Test command parse-environment_list
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

    # set the trace flag as true
    trace_true = "LIBGUESTFS_TRACE=1"
    parse_enviroment_list_result = gf.parse_environment_list(trace_true)
    logging.debug(parse_enviroment_list_result)

    get_trace_result = gf.get_trace().stdout.splitlines()
    logging.debug(get_trace_result[-1])
    if parse_enviroment_list_result.exit_status or not \
       get_trace_result[-1] == "true":
        gf.close_session()
        raise error.TestFail("failed to parse the environment %s" % trace_true)
    else:
        logging.info("Parse the environment %s successfully" % trace_true)

    # set the trace flag as false
    trace_false = "LIBGUESTFS_TRACE=0"
    parse_enviroment_list_result = gf.parse_environment_list(trace_false)

    get_trace_result = gf.get_trace().stdout.strip()
    logging.debug(get_trace_result)
    if parse_enviroment_list_result.exit_status or not \
       get_trace_result == "false":
        gf.close_session()
        raise error.TestFail("failed to parse the environment %s" % trace_false)
    else:
        logging.info("Parse the environment %s successfully" % trace_false)

    gf.close_session()


def test_rsync(vm, params):
    """
    Test command rsync
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
    gf.do_mount("/")

    # create files
    tmpdir_src = "/src/"
    tmpdir_dest = "/dest/"
    gf.rm_rf(tmpdir_src)
    gf.rm_rf(tmpdir_dest)
    gf.mkdir(tmpdir_src)
    gf.mkdir(tmpdir_dest)
    gf.write("%ssrc.txt" % tmpdir_src, "Hello World")
    gf.write("%sdest.txt" % tmpdir_dest, "hello world")

    # run "rsync src dest", should do nothing
    rsync_result = gf.rsync(tmpdir_src, tmpdir_dest, " ")
    logging.debug(rsync_result)
    ls_tmpdir_result = gf.ls(tmpdir_dest).stdout.splitlines()
    cat_result = gf.cat("%s%s" % (tmpdir_dest, ls_tmpdir_result[0]))
    logging.debug(len(ls_tmpdir_result))

    if rsync_result.exit_status or not len(ls_tmpdir_result) == 1 \
       or not cat_result.stdout.strip() == "hello world":
        gf.close_session()
        raise error.TestFail("failed to rsync the two directories")
    else:
        logging.info("rsync the two directories successfully")

    # run "rsync src dest archive:true"
    rsync_result = gf.rsync(tmpdir_src, tmpdir_dest, "archive:true")
    logging.debug(rsync_result)
    ls_tmpdir_result = gf.ls(tmpdir_dest).stdout.splitlines()
    logging.debug(len(ls_tmpdir_result))

    if rsync_result.exit_status or not len(ls_tmpdir_result) == 2:
        cat_result_0 = gf.cat("%s%s" % (tmpdir_dest, ls_tmpdir_result[0])).stdout.strip()
        cat_result_1 = gf.cat("%s%s" % (tmpdir_dest, ls_tmpdir_result[1])).stdout.strip()
        if not ((ls_tmpdir_result[0] == "src.txt" and
                 cat_result_0 == "Hello World" and
                 cat_result_1 == "hello world") or
                (ls_tmpdir_result[1] == "src.txt" and
                 cat_result_1 == "Hello World" and
                 cat_result_0 == "hello world")):
            gf.close_session()
            raise error.TestFail("failed to rsync the two directories")
    else:
        logging.info("rsync the two directories successfully")

    # run "rsync src dest archive:true deletedest:true"
    rsync_result = gf.rsync(tmpdir_src, tmpdir_dest,
                            "archive:true deletedest:true")
    logging.debug(rsync_result)
    ls_tmpdir_result = gf.ls(tmpdir_dest).stdout.splitlines()
    cat_result = gf.cat("%s%s" % (tmpdir_dest, ls_tmpdir_result[0]))
    logging.debug(len(ls_tmpdir_result))

    if rsync_result.exit_status or not len(ls_tmpdir_result) == 1 \
       or not cat_result.stdout.strip() == "Hello World":
        gf.close_session()
        raise error.TestFail("failed to rsync the two directories")
    else:
        logging.info("rsync the two directories successfully")

    gf.close_session()


def test_rsync_in(vm, params):
    """
    Test command rsync_in
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

    gf.set_network("true")
    gf.run()
    gf.do_mount("/")

    # create files
    tmpdir = data_dir.get_tmp_dir()
    tmpdir_src = os.path.join(tmpdir, "src/")
    commands.getoutput("rm -rf %s" % tmpdir_src)
    commands.getoutput("mkdir %s" % tmpdir_src)
    tmpfile_src = "%ssrc.txt" % tmpdir_src
    fd = open(tmpfile_src, "w+")
    fd.write("Hello World")
    fd.close()

    tmpdir_dest = "/dest/"
    gf.mkdir(tmpdir_dest)
    gf.write("%sdest.txt" % tmpdir_dest, "hello world")

    # construct the rsyncd.conf
    rsyncd_conf = "%s/rsyncd.conf" % tmpdir
    cmd_cfg_rsyncd = 'echo -e "port = 33335\n \
                      pid file = rsyncd.pid\n[src]\npath = %s\n \
                      comment = source\nuse chroot = false\n \
                      read only = true" > %s' % (tmpdir_src, rsyncd_conf)
    logging.debug(commands.getoutput(cmd_cfg_rsyncd))

    # get the host ip and config the rsync
    interface = commands.getoutput("route | awk '{if ($1==\"default\") print $8}'")
    ipaddr = commands.getoutput("ifconfig %s | grep -Eo \
                                'inet (addr:)?([0-9]*\.){3}[0-9]*'| \
                                 grep -Eo '([0-9]*\.){3}[0-9]*'" % interface)

    # kill the rsync
    pidof_rsync = commands.getoutput("pidof rsync")
    if pidof_rsync:
        commands.getstatusoutput("kill %s" % pidof_rsync)
    datadir = data_dir.get_data_dir()
    commands.getoutput("rm -rf %s/images/rsyncd.pid" % datadir)
    time.sleep(2)
    commands.getstatusoutput("rsync --daemon --config=%s" % rsyncd_conf)
    time.sleep(2)

    # run "rsync-in src dest", should do nothing
    rsync_in_result = gf.rsync_in("rsync://root@%s:33335/src/" % ipaddr,
                                  tmpdir_dest, " ")
    logging.debug(rsync_in_result)
    ls_tmpdir_result = gf.ls(tmpdir_dest).stdout.splitlines()
    cat_result = gf.cat("%s%s" % (tmpdir_dest, ls_tmpdir_result[0]))

    if rsync_in_result.exit_status or not len(ls_tmpdir_result) == 1 \
       or not cat_result.stdout.strip() == "hello world":
        commands.getoutput("rm -rf %s" % tmpdir_src)
        gf.close_session()
        raise error.TestFail("failed to rsync-in the two directories")
    else:
        logging.info("rsync-in the two directories successfully")

    # run "rsync-in src dest archive:true"
    rsync_in_result = gf.rsync_in("rsync://root@%s:33335/src/" % ipaddr,
                                  tmpdir_dest, "archive:true ")
    logging.debug(rsync_in_result)
    ls_tmpdir_result = gf.ls(tmpdir_dest).stdout.splitlines()

    if not len(ls_tmpdir_result) == 2:
        gf.close_session()
        raise error.TestFail("failed to rsync-in the two directories")
    cat_result_0 = gf.cat("%s%s" % (tmpdir_dest, ls_tmpdir_result[0]))
    cat_result_1 = gf.cat("%s%s" % (tmpdir_dest, ls_tmpdir_result[1]))

    if rsync_in_result.exit_status or not \
       ((ls_tmpdir_result[0] == "src.txt" and
         cat_result_0.stdout.strip() == "Hello World" and
         cat_result_1.stdout.strip() == "hello world") or
        (ls_tmpdir_result[1] == "src.txt" and
         cat_result_1.stdout.strip() == "Hello World" and
         cat_result_0.stdout.strip() == "hello world")):
        gf.close_session()
        raise error.TestFail("failed to rsync-in the two directories")
    else:
        logging.info("rsync-in the two directories successfully")

    # run "rsync-in src dest archive:true deletedest:true"
    rsync_in_result = gf.rsync_in("rsync://root@%s:33335/src/" % ipaddr,
                                  tmpdir_dest, "archive:true deletedest:true")
    logging.debug(rsync_in_result)
    ls_tmpdir_result = gf.ls(tmpdir_dest).stdout.splitlines()
    cat_result = gf.cat("%s%s" % (tmpdir_dest, ls_tmpdir_result[0]))

    if rsync_in_result.exit_status or not len(ls_tmpdir_result) == 1 \
       or not cat_result.stdout.strip() == "Hello World":
        commands.getoutput("rm -rf %s" % tmpdir_src)
        gf.close_session()
        raise error.TestFail("failed to rsync-in the two directories")
    else:
        logging.info("rsync-in the two directories successfully")

    # remove the tmp files on host
    commands.getoutput("rm -rf %s" % tmpdir_src)
    commands.getoutput("rm -rf %s" % rsyncd_conf)

    gf.close_session()


def test_rsync_out(vm, params):
    """
    Test command rsync_out
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

    gf.set_network("true")
    gf.run()
    gf.do_mount("/")

    # create files
    tmpdir = data_dir.get_tmp_dir()
    tmpdir_dest = os.path.join(tmpdir, "dest/")
    commands.getoutput("rm -rf %s" % tmpdir_dest)
    commands.getoutput("mkdir %s" % tmpdir_dest)
    commands.getoutput("chmod 777 %s" % tmpdir_dest)
    tmpfile_dest = "%sdest.txt" % tmpdir_dest
    fd = open(tmpfile_dest, "w+")
    fd.write("hello world")
    fd.close()

    tmpdir_src = "/src/"
    gf.mkdir(tmpdir_src)
    gf.write("%ssrc.txt" % tmpdir_src, "Hello World")

    # construct the rsyncd.conf
    rsyncd_conf = "%s/rsyncd.conf" % tmpdir
    cmd_cfg_rsyncd = 'echo -e "port = 33335\n \
                      pid file = rsyncd.pid\n[dest]\npath = %s\n \
                      comment = destination\nuse chroot = false\n \
                      read only = false\nuid = root\n \
                      gid = root" > %s' % (tmpdir_dest, rsyncd_conf)
    logging.debug(commands.getoutput(cmd_cfg_rsyncd))

    # get the host ip and config the rsync
    interface = commands.getoutput("route | awk '{if ($1==\"default\") print $8}'")
    ipaddr = commands.getoutput("ifconfig %s | grep -Eo \
                                'inet (addr:)?([0-9]*\.){3}[0-9]*'| \
                                 grep -Eo '([0-9]*\.){3}[0-9]*'" % interface)

    # kill the rsync
    pidof_rsync = commands.getoutput("pidof rsync")
    if pidof_rsync:
        commands.getoutput("kill %s" % pidof_rsync)
    datadir = data_dir.get_data_dir()
    commands.getoutput("rm -rf %s/images/rsyncd.pid" % datadir)
    time.sleep(2)
    commands.getoutput("rsync --daemon --config=%s" % rsyncd_conf)
    time.sleep(2)

    # run "rsync-out src dest", should do nothing
    rsync_out_result = gf.rsync_out(tmpdir_src, "rsync://root@%s:33335/dest/"
                                    % ipaddr, " ")
    logging.debug(rsync_out_result)
    ls_tmpdir_result = commands.getoutput("ls %s" % tmpdir_dest).splitlines()
    cat_result = commands.getoutput("cat %s%s" %
                                    (tmpdir_dest, ls_tmpdir_result[0]))

    if rsync_out_result.exit_status or not len(ls_tmpdir_result) == 1 \
       or not cat_result.strip() == "hello world":
        gf.close_session()
        raise error.TestFail("failed to rsync-out the two directories")
    else:
        logging.info("rsync-out the two directories successfully")

    # run "rsync-out src dest archive:true"
    rsync_out_result = gf.rsync_out(tmpdir_src, "rsync://root@%s:33335/dest/"
                                    % ipaddr, "archive:true")
    logging.debug(rsync_out_result)
    ls_tmpdir_result = commands.getoutput("ls %s" % tmpdir_dest).splitlines()

    if not len(ls_tmpdir_result) == 2:
        gf.close_session()
        raise error.TestFail("failed to rsync-out the two directories")
    cat_result_0 = commands.getoutput("cat %s%s" % (tmpdir_dest, ls_tmpdir_result[0]))
    cat_result_1 = commands.getoutput("cat %s%s" % (tmpdir_dest, ls_tmpdir_result[1]))

    if rsync_out_result.exit_status or not \
       ((ls_tmpdir_result[0] == "src.txt" and
         cat_result_0.strip() == "Hello World" and
         cat_result_1.strip() == "hello world") or
        (ls_tmpdir_result[1] == "src.txt" and
         cat_result_1.strip() == "Hello World" and
         cat_result_0.strip() == "hello world")):
        gf.close_session()
        raise error.TestFail("failed to rsync-out the two directories")
    else:
        logging.info("rsync-out the two directories successfully")

    # run "rsync-out src dest archive:true deletedest:true"
    rsync_out_result = gf.rsync_out(tmpdir_src, "rsync://root@%s:33335/dest/"
                                    % ipaddr, "archive:true deletedest:true")
    logging.debug(rsync_out_result)
    ls_tmpdir_result = commands.getoutput("ls %s" % tmpdir_dest).splitlines()
    cat_result = commands.getoutput("cat %s%s" %
                                    (tmpdir_dest, ls_tmpdir_result[0]))

    if rsync_out_result.exit_status or not len(ls_tmpdir_result) == 1 \
       or not cat_result.strip() == "Hello World":
        gf.close_session()
        raise error.TestFail("failed to rsync-out the two directories")
    else:
        logging.info("rsync-out the two directories successfully")

    # remove the tmp files on host
    commands.getoutput("rm -rf %s" % tmpdir_dest)
    commands.getoutput("rm -rf %s" % rsyncd_conf)

    gf.close_session()


def test_utimens(vm, params):
    """
    Test command utimens
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
    gf.do_mount("/")

    # create file
    tmpfile = "/src.txt"
    gf.write(tmpfile, "Hello World")

    # set the timestamp
    atsecs = "12345"
    atnsecs = "54321"
    mtsecs = "56789"
    mtnsecs = "98765"
    utimens_result = gf.utimens(tmpfile, atsecs, atnsecs, mtsecs, mtnsecs)
    stat_result = gf.stat(tmpfile).stdout.strip()
    logging.debug(stat_result)

    gf.close_session()

    if utimens_result.exit_status or not re.search("atime: 12345", stat_result) \
       or not re.search("mtime: 56789", stat_result):
        raise error.TestFail("failed to set the timestamp")
    else:
        logging.info("Set the timestamp successfully")


def test_utsname(vm, params):
    """
    Test command utsname
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

    utsname_result = gf.utsname()
    utsname_result_lines = utsname_result.stdout.splitlines()
    logging.debug(utsname_result)
    uts_sysname_result = utsname_result_lines[0].split(" ", 1)[1]
    uts_release_result = utsname_result_lines[1].split(" ", 1)[1]
    uts_version_result = utsname_result_lines[2].split(" ", 1)[1]
    uts_machine_result = utsname_result_lines[3].split(" ", 1)[1]
    uname_result = utils.run("uname -a").stdout.strip()
    logging.debug(uname_result)

    gf.close_session()

    if utsname_result.exit_status or not \
       re.search(uts_sysname_result, uname_result) or not \
       re.search(uts_release_result, uname_result) or not \
       re.search(uts_version_result, uname_result) or not \
       re.search(uts_machine_result, uname_result):
        raise error.TestFail("failed to get the appliance kernel version")
    else:
        logging.info("Get the appliance kernel version successfully")


def test_grub_install(vm, params):
    """
    Test command grub-install
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
    gf.do_mount("/")

    # get the mounted partition
    mounts_result = gf.mounts()
    logging.debug(mounts_result)
    mounted_patition_result = mounts_result.stdout.strip()
    if mounts_result.exit_status or not len(mounted_patition_result):
        gf.close_session()
        raise error.TestFail("Get partitions failed:%s" %
                             mounted_patition_result)

    # install grub on the mountes partition
    grub_install_result = gf.grub_install("/", mounted_patition_result)
    logging.debug(grub_install_result)
    ls_boot_result = gf.ls("/boot")
    ls_grub_result = gf.ls("/boot/grub")
    logging.debug(ls_boot_result)
    logging.debug(ls_grub_result)

    if grub_install_result.exit_status or ls_boot_result.exit_status \
       or ls_grub_result.exit_status:
        gf.close_session()
        raise error.TestFail("failed to install GRUB 1")
    else:
        logging.info("Install GRUB 1 successfully")

    gf.close_session()


def test_initrd_cat(vm, params):
    """
    Test command initrd-cat
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

    # mount the partition with os
    inspectos_result = gf.inspect_os()
    logging.debug(inspectos_result)
    roots_list = inspectos_result.stdout.splitlines()
    if inspectos_result.exit_status or not len(roots_list):
        gf.close_session()
        raise error.TestFail("Get os failed:%s" % inspectos_result)
    mount_result = gf.mount(roots_list[0], "/")
    logging.debug(mount_result)

    # get the /etc/passwd in the initrd image
    initrd_result = gf.glob_expand("/boot/init*.img")
    initrd_images = initrd_result.stdout.splitlines()
    if initrd_result.exit_status or not len(initrd_images):
        gf.close_session()
        raise error.TestFail("Get initrd failed:%s" % initrd_result)
    initrd_cat_result = gf.initrd_cat(initrd_images[0], "etc/passwd")
    logging.debug(initrd_cat_result)
    passwd_result = initrd_cat_result.stdout.splitlines()

    if initrd_cat_result.exit_status or not len(passwd_result):
        gf.close_session()
        raise error.TestFail("failed to initrd-cat /etc/passwd")
    else:
        logging.info("Initrd-cat /etc/passwd successfully")

    gf.close_session()


def test_initrd_list(vm, params):
    """
    Test command initrd-list
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

    # mount the partition with os
    inspectos_result = gf.inspect_os()
    logging.debug(inspectos_result)
    roots_list = inspectos_result.stdout.splitlines()
    if inspectos_result.exit_status or not len(roots_list):
        gf.close_session()
        raise error.TestFail("Get os failed:%s" % inspectos_result)
    mount_result = gf.mount(roots_list[0], "/")
    logging.debug(mount_result)

    # list the files in the initrd image
    initrd_result = gf.glob_expand("/boot/init*.img")
    initrd_images = initrd_result.stdout.splitlines()
    if initrd_result.exit_status or not len(initrd_images):
        gf.close_session()
        raise error.TestFail("Get initrd failed:%s" % initrd_result)
    initrd_list_result = gf.initrd_list(initrd_images[0])
    logging.debug(initrd_list_result)
    initrd_list_lines_result = initrd_list_result.stdout.splitlines()

    if initrd_list_result.exit_status or not len(initrd_list_lines_result):
        gf.close_session()
        raise error.TestFail("failed to initrd-list initrd.img")
    else:
        logging.info("Initrd-list initrd.img successfully")

    gf.close_session()


def test_compress_out(vm, params):
    """
    1) Fall into guestfish session w/ inspector
    2) Write a tempfile to guest
    3) Compress file to host with compress-out
    4) Check file on host
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
    gf.do_mount("/")

    content = "This is file for test of compress-out."
    path = "/test_compress_out"
    path_on_host = os.path.join(data_dir.get_tmp_dir(), "test_compress_out")

    # Create file
    write_result = gf.write(path, content)
    if write_result.exit_status:
        gf.close_session()
        raise error.TestFail("Create file failed.")
    logging.info("Create file successfully.")

    # remove the files on host
    commands.getoutput("rm -rf %s" % path_on_host)

    # Compress file to host
    ctypes = params.get("gf_ctype")
    logging.debug(ctypes)
    for ctype in re.findall("\w+", ctypes):
        compress_out_result = gf.compress_out(ctype, path, path_on_host)
        logging.debug(compress_out_result)
        if compress_out_result.exit_status:
            gf.close_session()
            raise error.TestFail("Compress out failed.")
        logging.info("Compress out successfully.")

        # Check the compressed file on host and delete it
        file_result = commands.getoutput("file %s" % path_on_host)
        logging.debug(file_result)
        commands.getoutput("rm -rf %s" % path_on_host)
        if not re.search(ctype.lower(), file_result) and \
           not re.search(ctype.upper(), file_result):
            gf.close_session()
            raise error.TestFail("File type do not match.")

    gf.close_session()


def test_compress_device_out(vm, params):
    """
    1) Fall into guestfish session w/ inspector
    2) Compress device to host with compress-device-out
    3) Check file on host
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
    gf.do_mount("/")

    content = "This is file for test of compress-device-out."
    path = "/test_compress_device_out"
    path_on_host = os.path.join(data_dir.get_tmp_dir(),
                                "test_compress_device_out")

    # Create file
    write_result = gf.write(path, content)
    if write_result.exit_status:
        gf.close_session()
        raise error.TestFail("Create file failed.")
    logging.info("Create file successfully.")

    # remove the files on host
    commands.getoutput("rm -rf %s" % path_on_host)

    # Get the mounted device
    mounts_result = gf.mounts()
    logging.debug(mounts_result)
    mounted_patition_result = mounts_result.stdout.strip()
    if mounts_result.exit_status or not len(mounted_patition_result):
        gf.close_session()
        raise error.TestFail("Get partitions failed:%s" %
                             mounted_patition_result)

    # Compress the device to host
    ctypes = params.get("gf_ctype")
    logging.debug(ctypes)
    for ctype in re.findall("\w+", ctypes):
        cdo_result = gf.compress_device_out(ctype, mounted_patition_result,
                                            path_on_host)
        logging.debug(cdo_result)
        if cdo_result.exit_status:
            gf.close_session()
            raise error.TestFail("Compress device out failed.")
        logging.info("Compress device out successfully.")

        # Check the compressed file on host and delete it
        file_result = commands.getoutput("file %s" % path_on_host)
        logging.debug(file_result)
        commands.getoutput("rm -rf %s" % path_on_host)
        if not re.search(ctype.lower(), file_result) and \
           not re.search(ctype.upper(), file_result):
            gf.close_session()
            raise error.TestFail("File type do not match.")

    gf.close_session()


def run(test, params, env):
    """
    Test of built-in lvm related commands in guestfish.

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
    logging.info(params)

    if params["need_os"] == "false":
        for image_format in re.findall("\w+", image_formats):
            params["image_format"] = image_format
            for partition_type in re.findall("\w+", partition_types):
                params["partition_type"] = partition_type
                prepare_image(params)
                testcase(vm, params)
    else:
        testcase(vm, params)
