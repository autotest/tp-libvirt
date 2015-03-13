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


def test_mke2fs(vm, params):
    """
    Test command mke2fs:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    partition_type = params.get("partition_type")
    test_mountpoint = params.get("mount_point")
    test_pv_name = params.get("pv_name")
    test_vg_name = params.get("vg_name")
    test_lv_name = params.get("lv_name")
    test_image_size = params.get("image_size")
    test_journal_size = params.get("journal_size")
    test_lv_size = (float(test_image_size.replace('G', '')) - float(test_journal_size.replace('G', ''))) * 1024 - 20

    uuid = "7e43b610-8882-43af-8a30-be54a0522a40"
    label = "mke2fs_test_disk"
    fs_array = ('ext2', 'ext3', 'ext4')

    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_image = img_dir + '/mk2fs_temp.img'
    os.system('qemu-img create %s %s > /dev/null' % (test_image, test_image_size))
    gf.add_drive_opts(test_image, readonly=readonly)
    gf.run()
    if partition_type == "lvm":
        test_journal_name = "vol_journal"
        gf.pvcreate(test_pv_name)
        gf.vgcreate(test_vg_name, test_pv_name)
        gf.lvcreate(test_lv_name, test_vg_name, int(test_lv_size))
        gf.lvcreate(test_journal_name, test_vg_name,
                    int(float(test_journal_size.replace('G', '')) * 1024))
    if partition_type == "physical":
        test_mountpoint = test_pv_name

    for test_fs in fs_array:
        gf.mke2fs(test_mountpoint, None, 2048, None, None, None, None, None, None,
                  None, None, None, None, None, None, None, label, None, None,
                  test_fs, None, uuid, 'true')
        gf.mount(test_mountpoint, '/')
        gf_result = gf.list_filesystems()
        expected_str = '%s: %s' % (test_mountpoint, test_fs)
        if expected_str not in gf_result.stdout:
            gf.close_session()
            os.system('rm -f %s' % test_image)
            logging.debug(gf_result)
            raise error.TestFail("test_mke2fs failed, filesystem not match")
        gf_result = gf.vfs_uuid(test_mountpoint)
        if uuid not in gf_result.stdout:
            gf.close_session()
            os.system('rm -f %s' % test_image)
            logging.debug(gf_result)
            raise error.TestFail("test_mke2fs failed, uuid not match")
        gf_result = gf.vfs_label(test_mountpoint)
        if label not in gf_result.stdout:
            gf.close_session()
            os.system('rm -f %s' % test_image)
            logging.debug(gf_result)
            raise error.TestFail("test_mke2fs failed, label not match")
        gf.umount("/")
    gf.close_session()
    os.system('rm -f %s' % test_image)


def test_mke2fs_J(vm, params):
    """
    Test command mke2journal, mke2journal_L, mke2journal_U, mke2fs_J,
    mke2fs_JU, mke2fs_JL:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    image_path = params.get("image_path")
    partition_type = params.get("partition_type")
    mk_journal_cmd = params.get("mk_journal_cmd")
    mk_fs_cmd = params.get("mk_fs_cmd")

    test_pv_name = params.get("pv_name")
    test_vg_name = params.get("vg_name")
    test_lv_name = params.get("lv_name")
    test_blocksize = params.get("blocksize")
    test_image_size = params.get("image_size")
    test_journal_size = params.get("journal_size")
    test_lv_size = (float(test_image_size.replace('G', '')) - float(test_journal_size.replace('G', ''))) * 1024 - 20

    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_image = img_dir + '/mk2fs_temp.img'
    os.system('qemu-img create %s %s > /dev/null' % (test_image, test_image_size))

    if partition_type == "physical":
        test_journal_name = "/dev/sdb"
        test_journal_device = test_journal_name
        test_mountpoint = test_pv_name
        test_journal_size_M = str(int(float(test_journal_size.replace('G', '')) * 1024))
        gf.alloc("journalname", ("%sM" % test_journal_size_M))
    gf.add_drive_opts(test_image, readonly=readonly)
    gf.run()

    # Create LVM if needed
    if partition_type == "lvm":
        test_journal_name = "vol_journal"
        test_mountpoint = params.get("mount_point")
        test_journal_device = "/dev/%s/%s" % (test_vg_name, test_journal_name)
        gf.pvcreate(test_pv_name)
        gf.vgcreate(test_vg_name, test_pv_name)
        gf.lvcreate(test_lv_name, test_vg_name, int(test_lv_size))
        gf.lvcreate(test_journal_name, test_vg_name,
                    int(float(test_journal_size.replace('G', '')) * 1024))

    # Create journal accordingly
    if mk_journal_cmd == "mke2journal":
        label_uuid = ""
        gf.mke2journal(test_blocksize, test_journal_device)
    elif mk_journal_cmd == "mke2journal_L":
        label_uuid = "test_journal"
        gf.mke2journal_L(test_blocksize, label_uuid, test_journal_device)
    elif mk_journal_cmd == "mke2journal_U":
        temp, label_uuid = commands.getstatusoutput("uuidgen")
        gf.mke2journal_U(test_blocksize, label_uuid, test_journal_device)

    # Check type of journal_device
    vfs_result = gf.vfs_type(test_journal_device)
    if vfs_result.stdout.strip() != "jbd":
        gf.close_session()
        os.system('rm -f %s' % test_image)
        logging.debug(vfs_result)
        raise error.TestFail("test_mke2fs_J failed, vfs_type not match")

    # Create Filesystem with external journal if needed, otherwise just return
    fs_array = ('ext2', 'ext3', 'ext4')
    for test_fs in fs_array:
        mk_fs_cmd = params.get("mk_fs_cmd")
        if mk_fs_cmd == "mke2fs_J":
            gf.mke2fs_J(test_fs, test_blocksize, test_mountpoint, test_journal_device)
        elif mk_fs_cmd == "mke2fs_JU":
            gf.mke2fs_JU(test_fs, test_blocksize, test_mountpoint, label_uuid)
        elif mk_fs_cmd == "mke2fs_JL":
            gf.mke2fs_JL(test_fs, test_blocksize, test_mountpoint, label_uuid)
        else:
            gf.close_session()
            os.system('rm -f %s' % test_image)
            return

        # check vfs type of newly created filesystem
        vfs_result = gf.vfs_type(test_mountpoint)
        if test_fs == "ext2" and vfs_result.stdout.strip() != "ext3":
            gf.close_session()
            os.system('rm -f %s' % test_image)
            logging.debug(vfs_result)
            raise error.TestFail("test_mke2fs_J failed, vfs_type not match")
        if test_fs != "ext2" and test_fs != vfs_result.stdout.strip():
            gf.close_session()
            os.system('rm -f %s' % test_image)
            logging.debug(vfs_result)
            raise error.TestFail("test_mke2fs_J failed, vfs_type not match")
    gf.close_session()
    os.system('rm -f %s' % test_image)


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
                prepare_image(params)
            testcase(vm, params)
