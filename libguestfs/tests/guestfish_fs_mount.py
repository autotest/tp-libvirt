import logging
import os
import re

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


def test_mount(test, vm, params):
    """
    Test command mount:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    gf.run()

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    partition_type = params.get("partition_type")
    vg_name = params.get("vg_name")
    lv_name = params.get("lv_name")
    if partition_type == 'lvm':
        abs_mount_point = '/dev/mapper/%s-%s' % (vg_name, lv_name)
    elif partition_type == 'physical':
        abs_mount_point = '%s1' % pv_name
    else:
        gf.close_session()
        test.fail("test_mount failed, Wrong partition type")

    gf_result = gf.inner_cmd("debug sh 'mount|grep %s'" % abs_mount_point)
    if gf_result.exit_status != 0 or ('/sysroot' not in gf_result.stdout):
        gf.close_session()
        test.fail("test_mount failed")
    gf.close_session()


def test_mount_options(test, vm, params):
    """
    Test command mount_options:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    gf.run()

    mount_point = params.get("mount_point")
    gf.mount_options('noatime', mount_point, '/')

    partition_type = params.get("partition_type")
    vg_name = params.get("vg_name")
    lv_name = params.get("lv_name")
    if partition_type == 'lvm':
        abs_mount_point = '/dev/mapper/%s-%s' % (vg_name, lv_name)
    elif partition_type == 'physical':
        abs_mount_point = '%s1' % pv_name
    else:
        gf.close_session()
        test.fail("test_mount_options failed, Wrong partition type")

    gf_result = gf.inner_cmd("debug sh 'mount|grep %s'" % abs_mount_point)
    if gf_result.exit_status != 0 or ('/sysroot' not in gf_result.stdout) or (
       'noatime' not in gf_result.stdout):
        gf.close_session()
        test.fail("test_mount_options failed")
    gf.close_session()


def test_mount_ro(test, vm, params):
    """
    Test command mount_ro:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    gf.run()

    mount_point = params.get("mount_point")
    gf.mount_ro(mount_point, '/')

    partition_type = params.get("partition_type")
    vg_name = params.get("vg_name")
    lv_name = params.get("lv_name")
    if partition_type == 'lvm':
        abs_mount_point = '/dev/mapper/%s-%s' % (vg_name, lv_name)
    elif partition_type == 'physical':
        abs_mount_point = '%s1' % pv_name
    else:
        gf.close_session()
        test.fail("test_mount_ro failed, Wrong partition type")

    gf_result = gf.inner_cmd("debug sh 'mount|grep %s'" % abs_mount_point)
    if gf_result.exit_status != 0 or ('/sysroot' not in gf_result.stdout) or (
       'ro' not in gf_result.stdout):
        gf.close_session()
        test.fail("test_mount_ro failed")
    gf.close_session()


def test_mounts(test, vm, params):
    """
    Test command mounts:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    gf.run()
    mount_point = params.get("mount_point")
    gf.mount_ro(mount_point, '/')

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')
    gf_result = gf.mounts()
    if gf_result.exit_status != 0 or (mount_point not in gf_result.stdout):
        gf.close_session()
        test.fail("test_mounts failed")
    gf.close_session()


def test_mount_loop(test, vm, params):
    """
    Test command mount_loop:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = test_dir + '/test_mount_loop.img'
    test_img_iso = test_img + ".iso"
    os.system('qemu-img create -f ' + ' raw ' +
              test_img + ' 50M > /dev/null')
    os.system('mkisofs -r -J -V test -o %s %s > /dev/null' % (test_img_iso, test_img))

    pv_name = params.get("pv_name")
    gf.run()

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')
    gf.upload(test_img_iso, '/test_mount_loop.iso')
    gf.mkmountpoint('/loop')
    mount_loop_result = gf.mount_loop('/test_mount_loop.iso', '/loop')
    readdir_result = gf.readdir('/loop')
    gf.umount('/loop')
    gf.rmmountpoint('/loop')
    os.system('rm -f %s; rm -f %s' % (test_img_iso, test_img))

    if mount_loop_result.exit_status != 0 or ('test_mount_loop.img' not in readdir_result.stdout):
        gf.close_session()
        logging.error("mount_loop /test_mount_loop.iso /loop:")
        logging.error(mount_loop_result)
        logging.error("readdir /loop:")
        logging.error(readdir_result)
        test.fail("test_mount_loop failed")
    gf.close_session()


def test_mountpoints(test, vm, params):
    """
    Test command mountpoints:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    gf.run()
    mount_point = params.get("mount_point")

    gf.mount(mount_point, '/')
    gf.mkmountpoint('/test_mountpoints')
    gf_result = gf.mountpoints()
    expected_str = '%s: /' % mount_point
    if expected_str not in gf_result.stdout:
        gf.close_session()
        logging.error("Expected string: %s" % expected_str)
        logging.error('mountpoints:')
        logging.error(gf_result)
        test.fail("test_mountpoints failed")
    gf.close_session()


def test_umount(test, vm, params):
    """
    Test command umount:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    gf.run()
    mount_point = params.get("mount_point")

    # test umount
    mounts_result = []

    gf.mount(mount_point, '/')
    gf.umount(mount_point, 'true')
    gf_result = gf.mounts()
    mounts_result.append(gf_result.stdout)

    gf.mount(mount_point, '/')
    gf.umount(mount_point, 'false')
    gf_result = gf.mounts()
    mounts_result.append(gf_result.stdout)

    gf.mount(mount_point, '/')
    gf.umount(mount_point, None, 'true')
    gf_result = gf.mounts()
    mounts_result.append(gf_result.stdout)

    gf.mount(mount_point, '/')
    gf.umount(mount_point, None, 'false')
    gf_result = gf.mounts()
    mounts_result.append(gf_result.stdout)

    gf.mount(mount_point, '/')
    gf.umount(mount_point, 'false', 'false')
    gf_result = gf.mounts()
    mounts_result.append(gf_result.stdout)

    gf.mount(mount_point, '/')
    gf.umount(mount_point, 'true', 'true')
    gf_result = gf.mounts()
    mounts_result.append(gf_result.stdout)

    gf.mount(mount_point, '/')
    gf.umount(mount_point, 'true', 'false')
    gf_result = gf.mounts()
    mounts_result.append(gf_result.stdout)

    gf.mount(mount_point, '/')
    gf.umount(mount_point, 'false', 'true')
    gf_result = gf.mounts()
    mounts_result.append(gf_result.stdout)

    for items in mounts_result:
        if mount_point in items:
            gf.close_session()
            logging.error("'mounts' result:'")
            logging.error(items)
            test.fail("test_umount failed")
    gf.close_session()


def test_mount_vfs(test, vm, params):
    """
    Test command mount_vfs:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    gf.run()

    fs_type = params.get("fs_type")
    mount_point = params.get("mount_point")
    mount_vfs_result = gf.mount_vfs("\"\"", fs_type,  mount_point, '/')
    partition_type = params.get("partition_type")
    vg_name = params.get("vg_name")
    lv_name = params.get("lv_name")
    if partition_type == 'lvm':
        abs_mount_point = '/dev/mapper/%s-%s' % (vg_name, lv_name)
    elif partition_type == 'physical':
        abs_mount_point = '%s1' % pv_name
    else:
        gf.close_session()
        test.fail("test_mount_vfs failed, Wrong partition type")

    gf_result = gf.inner_cmd("debug sh 'mount|grep %s'" % abs_mount_point)
    if mount_vfs_result.exit_status != 0 or ('/sysroot' not in gf_result.stdout):
        gf.close_session()
        logging.error(mount_vfs_result)
        logging.error(gf_result)
        test.fail("test_mount_vfs failed")
    gf.close_session()


def test_umount_all(test, vm, params):
    """
    Test command umount_all:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    gf.run()

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')
    gf.umount_all()
    mounts_result = gf.mounts()
    if mount_point in mounts_result.stdout:
        gf.close_session()
        logging.error(mounts_result)
        test.fail("test_umount_all failed")
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
                prepare_image(test, params)
            testcase(test, vm, params)
