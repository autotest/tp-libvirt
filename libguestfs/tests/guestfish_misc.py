import re

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

    gf = utils_test.libguestfs.GuestfishTools(params)
    status, output = gf.create_fs()
    if status is False:
        gf.close_session()
        test.fail(output)
    gf.close_session()


def test_df(test, vm, params):
    """
    Test command df
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
    gf.do_mount("/")
    result = gf.df().stdout.strip()

    gf.close_session()

    physical_name = "/dev/sda"
    lvm_name = "/dev/mapper/vol_test-vol_file"

    if physical_name not in result and lvm_name not in result:
        test.fail("Could not find df output")


def test_df_h(test, vm, params):
    """
    Test command df-h
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
    gf.do_mount("/")
    result = gf.df_h().stdout.strip()

    gf.close_session()

    physical_name = "/dev/sda"
    lvm_name = "/dev/mapper/vol_test-vol_file"

    if physical_name not in result and lvm_name not in result:
        test.fail("Could not find df output")

    if not re.search("\d+[MKG]", result):
        test.fail("It's not a human-readable format")


def test_dd(test, vm, params):
    """
    Test command dd
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
    gf.do_mount("/")

    gf.write("/src.txt", "Hello World")
    gf.dd("/src.txt", "/dest.txt")

    src_size = gf.filesize("/src.txt").stdout.strip()
    dest_size = gf.filesize("/dest.txt").stdout.strip()
    dest_content = gf.cat("/dest.txt").stdout.strip()

    gf.close_session()

    if src_size != dest_size or dest_content != "Hello World":
        test.fail("Content or filesize is not match")


def test_copy_size(test, vm, params):
    """
    Test command copy-size
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
    gf.do_mount("/")

    gf.write("/src.txt", "Hello World")
    src_size = gf.filesize("/src.txt").stdout.strip()

    gf.copy_size("/src.txt", "/dest.txt", src_size)
    dest_size = gf.filesize("/dest.txt").stdout.strip()
    dest_content = gf.cat("/dest.txt").stdout.strip()

    gf.close_session()

    if src_size != dest_size or dest_content != "Hello World":
        test.fail("Content or filesize is not match")


def test_download(test, vm, params):
    """
    Test command download
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
    gf.do_mount("/")

    gf.write("/src.txt", "Hello World")
    src_size = gf.filesize("/src.txt").stdout.strip()

    dest = "%s/dest.txt" % data_dir.get_tmp_dir()
    gf.download("/src.txt", "%s" % dest)
    gf.close_session()

    content = process.getoutput("cat %s" % dest)
    process.system("rm %s" % dest)

    if content != "Hello World":
        test.fail("Content or filesize is not match")


def test_download_offset(test, vm, params):
    """
    Test command download-offset
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
    gf.do_mount("/")

    string = "Hello World"

    gf.write("/src.txt", string)
    src_size = gf.filesize("/src.txt").stdout.strip()

    dest = "%s/dest.txt" % data_dir.get_tmp_dir()
    gf.download_offset("/src.txt", "%s" % dest, 0, len(string))
    gf.close_session()

    content = process.getoutput("cat %s" % dest)
    process.system("rm %s" % dest)

    if content != "Hello World":
        test.fail("Content or filesize is not match")


def test_upload(test, vm, params):
    """
    Test command upload
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
    gf.do_mount("/")

    filename = "%s/src.txt" % data_dir.get_tmp_dir()
    with open(filename, "w+") as fd:
        fd.write("Hello World")

    gf.upload(filename, "/dest.txt")
    content = gf.cat("/dest.txt").stdout.strip()
    gf.close_session()
    process.getoutput("rm %s" % filename)

    if content != "Hello World":
        test.fail("Content is not correct")


def test_upload_offset(test, vm, params):
    """
    Test command upload_offset
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
    gf.do_mount("/")

    filename = "%s/src.txt" % data_dir.get_tmp_dir()
    string = "Hello World"
    process.getoutput("echo %s > %s" % (string, filename))

    gf.upload_offset(filename, "/dest.txt", 0)
    content = gf.cat("/dest.txt").stdout.strip()
    gf.close_session()
    process.getoutput("rm %s" % filename)

    if content != string:
        test.fail("Content is not correct")


def test_fallocate(test, vm, params):
    """
    Test command fallocate
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
    gf.do_mount("/")

    # preallocates a new file
    gf.fallocate("/new", 100)
    size = gf.filesize("/new").stdout.strip()

    if int(size) != 100:
        gf.close_session()
        test.fail("File size is not correct")

    # overwriteen a existed file
    string = "Hello World"
    gf.write("/exist", "Hello world")
    gf.fallocate("/exist", 200)
    size = gf.filesize("/exist").stdout.strip()
    content = gf.cat("/exist").stdout.strip()
    gf.close_session()

    if content != "":
        test.fail("Content is not empty")
    if int(size) != 200:
        test.fail("File size is not correct")


def test_fallocate64(test, vm, params):
    """
    Test command fallocate64
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
    gf.do_mount("/")

    # preallocates a new file
    gf.fallocate("/new", 100)
    size = gf.filesize("/new").stdout.strip()

    if int(size) != 100:
        gf.close_session()
        test.fail("File size is not correct")

    # overwriteen a existed file
    string = "Hello World"
    gf.write("/exist", "Hello world")
    gf.fallocate64("/exist", 200)
    size = gf.filesize("/exist").stdout.strip()
    content = gf.cat("/exist").stdout.strip()
    gf.close_session()

    if content != "":
        test.fail("Content is not empty")
    if int(size) != 200:
        test.fail("File size is not correct")


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

    for image_format in re.findall("\w+", image_formats):
        params["image_format"] = image_format
        for partition_type in re.findall("\w+", partition_types):
            params["partition_type"] = partition_type
            prepare_image(test, params)
            testcase(test, vm, params)
