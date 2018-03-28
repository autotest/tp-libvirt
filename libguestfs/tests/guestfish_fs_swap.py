import logging
import os
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


def test_swap_device(test, vm, params):
    """
    Test mkswap, swapon_device and swapoff_device:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    partition_type = params.get("partition_type")
    vg_name = params.get("vg_name")
    lv_name = params.get("lv_name")

    test_format = params.get("image_format")
    test_size = params.get("image_size")
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = test_dir + '/test_swap_device.img'
    test_pv = '/dev/sdb'
    os.system('qemu-img create -f ' + test_format + ' ' +
              test_img + ' ' + test_size + ' > /dev/null')
    gf.add_drive(test_img)
    gf.run()

    # create pv and vg
    if partition_type == "lvm":
        test_vg = "mkswap_testvg"
        test_lv = "mkswap_testlv"
        test_mountpoint = "/dev/%s/%s" % (test_vg, test_lv)
        if 'G' in test_size:
            test_lv_size = int(test_size.replace('G', '')) * 1000
        else:
            test_lv_size = int(test_size.replace('M', '')) - 10
        gf.pvcreate(test_pv)
        gf.vgcreate(test_vg, test_pv)
        gf.lvcreate(test_lv, test_vg, test_lv_size)
    elif partition_type == "physical":
        test_mountpoint = test_pv + "1"
        gf.part_disk(test_pv, "mbr")
        gf.part_list(test_pv)

    label = 'mkswap_label'
    uuid = '71631235-fbec-4ce8-b1b3-c53366f5ac60'
    mkswap_result = gf.mkswap(test_mountpoint, label, uuid)
    file_result = gf.file(test_mountpoint)
    if "swap file" not in file_result.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(mkswap_result)
        logging.error(file_result)
        test.fail("test_mkswap failed")
    if label not in file_result.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error("swap file label created not match")
        logging.error(file_result)
        test.fail("test_mkswap failed")
    if uuid not in file_result.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error("swap file uuid created not match")
        logging.error(file_result)
        test.fail("test_mkswap failed")

    gf.swapon_device(test_mountpoint)
    active_swapdevice = gf.inner_cmd("debug sh \"swapon -sv\" | sed -n '2p' ")
    if '/dev' not in active_swapdevice.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(active_swapdevice)
        test.fail("test_swapon_device failed")

    gf.swapoff_device(test_mountpoint)
    active_swapdevice = gf.inner_cmd("debug sh \"swapon -sv\" | sed -n '2p' ")

    if '/dev' in active_swapdevice.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(active_swapdevice)
        test.fail("test_swapoff_device failed")

    os.system('rm -f ' + test_img + ' > /dev/null')
    gf.close_session()


def test_swap_label(test, vm, params):
    """
    Test mkswap_L, swapon_label and swapoff_label:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    partition_type = params.get("partition_type")
    vg_name = params.get("vg_name")
    lv_name = params.get("lv_name")

    test_format = params.get("image_format")
    test_size = params.get("image_size")
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = test_dir + '/test_swap_device.img'
    test_pv = '/dev/sdb'
    os.system('qemu-img create -f ' + test_format + ' ' +
              test_img + ' ' + test_size + ' > /dev/null')
    gf.add_drive(test_img)
    gf.run()

    # create pv and vg
    if partition_type == "lvm":
        test_vg = "mkswap_testvg"
        test_lv = "mkswap_testlv"
        test_mountpoint = "/dev/%s/%s" % (test_vg, test_lv)
        if 'G' in test_size:
            test_lv_size = int(test_size.replace('G', '')) * 1000
        else:
            test_lv_size = int(test_size.replace('M', '')) - 10
        gf.pvcreate(test_pv)
        gf.vgcreate(test_vg, test_pv)
        gf.lvcreate(test_lv, test_vg, test_lv_size)
    elif partition_type == "physical":
        test_mountpoint = test_pv + "1"
        gf.part_disk(test_pv, "mbr")
        gf.part_list(test_pv)

    label = 'mkswap_label'
    mkswap_L_result = gf.mkswap_L(label, test_mountpoint)
    file_result = gf.file(test_mountpoint)
    if "swap file" not in file_result.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(mkswap_L_result)
        logging.error(file_result)
        test.fail("test_mkswap_L failed")
    if label not in file_result.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error("swap file label created not match")
        logging.error(file_result)
        test.fail("test_mkswap_L failed")

    gf.swapon_label(label)
    active_swapdevice = gf.inner_cmd("debug sh \"swapon -sv\" | sed -n '2p' ")
    if '/dev' not in active_swapdevice.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(active_swapdevice)
        test.fail("test_swapon_label failed")

    gf.swapoff_label(label)
    active_swapdevice = gf.inner_cmd("debug sh \"swapon -sv\" | sed -n '2p' ")

    if '/dev' in active_swapdevice.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(active_swapdevice)
        test.fail("test_swapoff_label failed")

    os.system('rm -f ' + test_img + ' > /dev/null')
    gf.close_session()


def test_swap_uuid(test, vm, params):
    """
    Test mkswap_L, swapon_uuid and swapoff_uuid:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    partition_type = params.get("partition_type")
    vg_name = params.get("vg_name")
    lv_name = params.get("lv_name")

    test_format = params.get("image_format")
    test_size = params.get("image_size")
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = test_dir + '/test_swap_device.img'
    test_pv = '/dev/sdb'
    os.system('qemu-img create -f ' + test_format + ' ' +
              test_img + ' ' + test_size + ' > /dev/null')
    gf.add_drive(test_img)
    gf.run()

    # create pv and vg
    if partition_type == "lvm":
        test_vg = "mkswap_testvg"
        test_lv = "mkswap_testlv"
        test_mountpoint = "/dev/%s/%s" % (test_vg, test_lv)
        if 'G' in test_size:
            test_lv_size = int(test_size.replace('G', '')) * 1000
        else:
            test_lv_size = int(test_size.replace('M', '')) - 10
        gf.pvcreate(test_pv)
        gf.vgcreate(test_vg, test_pv)
        gf.lvcreate(test_lv, test_vg, test_lv_size)
    elif partition_type == "physical":
        test_mountpoint = test_pv + "1"
        gf.part_disk(test_pv, "mbr")
        gf.part_list(test_pv)

    temp, uuid = process.getstatusoutput("uuidgen")
    mkswap_U_result = gf.mkswap_U(uuid, test_mountpoint)
    file_result = gf.file(test_mountpoint)
    if "swap file" not in file_result.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(mkswap_U_result)
        logging.error(file_result)
        test.fail("test_mkswap_U failed")
    if uuid not in file_result.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error("swap file uuid created not match")
        logging.error(file_result)
        test.fail("test_mkswap_U failed")

    gf.swapon_uuid(uuid)
    active_swapdevice = gf.inner_cmd("debug sh \"swapon -sv\" | sed -n '2p' ")
    if '/dev' not in active_swapdevice.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(active_swapdevice)
        test.fail("test_swapon_uuid failed")

    gf.swapoff_uuid(uuid)
    active_swapdevice = gf.inner_cmd("debug sh \"swapon -sv\" | sed -n '2p' ")

    if '/dev' in active_swapdevice.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(active_swapdevice)
        test.fail("test_swapoff_uuid failed")

    os.system('rm -f ' + test_img + ' > /dev/null')
    gf.close_session()


def test_swap_file(test, vm, params):
    """
    Test mkswap_L, swapon_label and swapoff_label:
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)

    image_path = params.get("image_path")
    gf.add_drive_opts(image_path, readonly=readonly)

    pv_name = params.get("pv_name")
    partition_type = params.get("partition_type")
    vg_name = params.get("vg_name")
    lv_name = params.get("lv_name")

    test_format = params.get("image_format")
    test_size = params.get("image_size")
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = test_dir + '/test_swap_device.img'
    test_pv = '/dev/sdb'
    os.system('qemu-img create -f ' + test_format + ' ' +
              test_img + ' ' + test_size + ' > /dev/null')
    gf.add_drive(test_img)
    gf.run()

    # create pv and vg
    if 'G' in test_size:
        test_lv_size = int(test_size.replace('G', '')) * 1000
    else:
        test_lv_size = int(test_size.replace('M', '')) - 10
    if partition_type == "lvm":
        test_vg = "mkswap_testvg"
        test_lv = "mkswap_testlv"
        test_mountpoint = "/dev/%s/%s" % (test_vg, test_lv)
        gf.pvcreate(test_pv)
        gf.vgcreate(test_vg, test_pv)
        gf.lvcreate(test_lv, test_vg, test_lv_size)
    elif partition_type == "physical":
        test_mountpoint = test_pv + "1"
        gf.part_disk(test_pv, "mbr")
        gf.part_list(test_pv)

    gf.mkfs('ext3', test_mountpoint)
    gf.mount(test_mountpoint, '/')
    swapfile = '/swap_file'
    swapfile_size = test_lv_size - 30
    gf.fallocate(swapfile, swapfile_size*1024)
    mkswap_file_result = gf.mkswap_file(swapfile)
    file_result = gf.file(swapfile)
    if "swap file" not in file_result.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(mkswap_file_result)
        logging.error(file_result)
        test.fail("test_mkswap_file failed")

    gf.swapon_file(swapfile)
    active_swapdevice = gf.inner_cmd("debug sh \"swapon -sv\" | sed -n '2p' ")
    if swapfile not in active_swapdevice.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(active_swapdevice)
        test.fail("test_swapon_file failed")

    gf.swapoff_file(swapfile)
    active_swapdevice = gf.inner_cmd("debug sh \"swapon -sv\" | sed -n '2p' ")

    if swapfile in active_swapdevice.stdout:
        gf.close_session()
        os.system('rm -f ' + test_img + ' > /dev/null')
        logging.error(active_swapdevice)
        test.fail("test_swapoff_file failed")

    os.system('rm -f ' + test_img + ' > /dev/null')
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
