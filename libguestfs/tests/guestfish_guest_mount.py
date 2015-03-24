from autotest.client.shared import error, utils
from virttest import utils_test, utils_misc, data_dir
from virttest.tests import unattended_install
from virttest import virsh, qemu_storage
from virttest import utils_test
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


def test_guestmount(vm, params):
    """
    Test command guestmount:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    vt = utils_test.libguestfs.VirtTools(vm, params)

    image_path = params.get("image_path")
    source_image = params.get("source_image")
    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_image = img_dir + "/guestmount_test.img"
    host_mountpoint = img_dir + "/guestmountpoint"
    source_image = img_dir + '/../shared/data/images/' + source_image
    os.system('mkdir %s' % host_mountpoint)
    os.system('cp %s %s > /dev/null' % (source_image, test_image))
    vt_result = vt.guestmount(host_mountpoint, test_image, inspector=True,
                              readonly=False, is_disk=True)

    # count file number
    temp, content = commands.getstatusoutput("ls %s|wc -l" % host_mountpoint)
    if int(content) == 0:
        os.system('umount %s > /dev/null 2>&1' % host_mountpoint)
        gf.close_session()
        os.system('rm -f %s' % host_mountpoint)
        os.system('rm -f %s' % test_image)
        logging.error(content)
        logging.error(vt_result)
        raise error.TestFail("test_guestmount failed")

    # write file
    os.system("echo 1234 > %s/guestmount-test" % host_mountpoint)
    temp, content = commands.getstatusoutput("cat %s/guestmount-test" % host_mountpoint)
    if content.strip() != '1234':
        os.system('umount %s > /dev/null 2>&1' % host_mountpoint)
        gf.close_session()
        os.system('rm -f %s' % host_mountpoint)
        os.system('rm -f %s' % test_image)
        logging.error(content)
        raise error.TestFail("test_guestmount failed")

    os.system('umount %s > /dev/null 2>&1' % host_mountpoint)
    gf.close_session()
    os.system('rm -f %s' % host_mountpoint)
    os.system('rm -f %s' % test_image)


def test_guestmount_ro(vm, params):
    """
    Test command guestmount_ro:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    vt = utils_test.libguestfs.VirtTools(vm, params)

    image_path = params.get("image_path")
    source_image = params.get("source_image")
    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    source_image = img_dir + '/../shared/data/images/' + source_image
    test_image = img_dir + "/guestmountro_test.img"
    host_mountpoint = img_dir + "/guestmountropoint"
    os.system('mkdir %s' % host_mountpoint)
    os.system('cp %s %s > /dev/null' % (source_image, test_image))
    vt_result = vt.guestmount(host_mountpoint, test_image, inspector=True,
                              readonly=True, is_disk=True)

    # count file number
    temp, content = commands.getstatusoutput("ls %s|wc -l" % host_mountpoint)
    if int(content) == 0:
        os.system('umount %s > /dev/null 2>&1' % host_mountpoint)
        gf.close_session()
        os.system('rm -f %s' % host_mountpoint)
        os.system('rm -f %s' % test_image)
        logging.error(content)
        raise error.TestFail("test_guestmount_ro failed")

    # touch file
    temp, content = commands.getstatusoutput("touch %s/guestmountro-test" % host_mountpoint)
    if temp == 0:
        os.system('umount %s > /dev/null 2>&1' % host_mountpoint)
        gf.close_session()
        os.system('rm -f %s' % host_mountpoint)
        os.system('rm -f %s' % test_image)
        logging.error(content)
        raise error.TestFail("test_guestmount_ro failed")

    os.system('umount %s > /dev/null 2>&1' % host_mountpoint)
    gf.close_session()
    os.system('rm -f %s' % host_mountpoint)
    os.system('rm -f %s' % test_image)


def test_guestmount_regression(vm, params):
    """
    Test command guestmount_regression:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    vt = utils_test.libguestfs.VirtTools(vm, params)

    image_path = params.get("image_path")
    source_image = params.get("source_image")
    img_dir = params.get("img_dir", data_dir.get_tmp_dir())
    source_image = img_dir + '/../shared/data/images/' + source_image
    nfs_file = "gr_nfs_file"
    nfs_remote = img_dir + "/gr_nfs_remote"
    nfs_remote_file = nfs_remote + '/' + nfs_file
    nfs_local = img_dir + "/gr_nfs_local"
    nfs_local_file = nfs_local + "/" + nfs_file
    host_mountpoint = img_dir + "/gr_test_mountpoint"
    os.system('mkdir %s' % nfs_remote)
    os.system('cp %s %s' % (source_image, nfs_remote_file))
    os.system('truncate --size=10G %s' % nfs_remote_file)
    ip_str = "ifconfig|grep inet|grep netmask|grep -v '127.0.0.1'|awk '{print $2}'"
    temp, ip = commands.getstatusoutput(ip_str)
    os.system('mkdir %s' % nfs_local)
    os.system('service nfs start')
    os.system("exportfs -v -o rw,no_root_squash *:%s " % nfs_remote)
    os.system("mount %s:%s %s" % (ip, nfs_remote, nfs_local))
    os.system('mkdir %s' % host_mountpoint)
    os.system('dd if=%s of=%s/gr_test_img1 bs=1M &' % (nfs_local_file, img_dir))
    time.sleep(1)
    os.system('dd if=%s of=%s/gr_test_img2 bs=1M &' % (nfs_local_file, img_dir))
    time.sleep(1)
    os.system('dd if=%s of=%s/gr_test_img3 bs=1M &' % (nfs_local_file, img_dir))
    time.sleep(1)
    vt_status, vt_result = vt.guestmount(host_mountpoint, nfs_local_file, inspector=True,
                                         readonly=False, is_disk=True, timeout=30)

    if vt_status is not True:
        logging.error(vt_status)
        logging.error(vt_result)
        gf.close_session()
        os.system("ps ax |grep dd |grep '$%s' |awk '{print $1}'|xargs kill" % nfs_file)
        time.sleep(3)
        os.system('umount %s > /dev/null 2>&1' % host_mountpoint)
        os.system('umount %s > /dev/null 2>&1' % nfs_local)
        os.system('rm -f %s' % host_mountpoint)
        os.system('rm -rf %s' % nfs_local)
        os.system('rm -rf %s' % nfs_remote)
        raise error.TestFail("test_guestmount_regression failed")
    gf.close_session()
    logging.debug(vt_result)
    logging.debug(vt_status)
    os.system("ps ax |grep dd |grep '$%s' |awk '{print $1}'|xargs kill" % nfs_file)
    time.sleep(3)
    os.system('umount %s > /dev/null 2>&1' % host_mountpoint)
    os.system('umount %s > /dev/null 2>&1' % nfs_local)
    os.system('rm -rf %s' % host_mountpoint)
    os.system('rm -rf %s' % nfs_local)
    os.system('rm -rf %s' % nfs_remote)


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
