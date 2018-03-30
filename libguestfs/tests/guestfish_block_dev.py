import logging
import os
import re

from virttest import utils_test
from virttest import data_dir
from virttest import qemu_storage


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


def test_blockdev_flushbufs(test, vm, params):
    """
    Test blockdev-flushbufs command
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
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_flushbufs(pv_name)
    logging.debug(gf_result)
    gf.do_mount("/")
    gf.touch("/test")
    gf.ll("/")
    if gf_result.exit_status:
        gf.close_session()
        test.fail("blockdev_flushbufs failed.")
    logging.info("Get readonly status successfully.")
    gf.close_session()


def test_blockdev_set_get_ro_rw(test, vm, params):
    """
    Set block device to read-only or read-write

    (1)blockdev-setro
    (2)blockdev-getro
    (3)blockdev-setrw
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")
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
            test.fail("Get the uncorrect status, test failed.")
    finally:
        gf.close_session()


def test_blockdev_getsz(test, vm, params):
    """
    Test command blockdev_getsz
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
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_getsz(pv_name).stdout.strip()
    gf.close_session()

    get_size = (int(gf_result) * 512)
    power = {'G': 1024 ** 3, "M": 1024 ** 2, "K": 1024}
    image_size = params["image_size"]
    image_size = int(image_size[:-1]) * power[image_size[-1]]
    logging.debug("Image size is %d" % image_size)
    logging.debug("Get size is %d" % get_size)

    if image_size != get_size:
        test.fail("Can not get the correct result.")


def test_blockdev_getbsz(test, vm, params):
    """
    Test command blockdev_getbsz
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
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_getbsz(pv_name).stdout.strip()
    gf.close_session()

    logging.debug("Block size of %s is %d" % (pv_name, int(gf_result)))

    if int(gf_result) != 4096:
        test.fail("Block size should be 4096")


def test_blockdev_getss(test, vm, params):
    """
    Test command blockdev_getbss
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
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_getss(pv_name).stdout.strip()
    gf.close_session()

    logging.debug("Size of %s is %d" % (pv_name, int(gf_result)))

    if int(gf_result) < 512:
        test.fail("Size of sectors is uncorrect")


def test_blockdev_getsize64(test, vm, params):
    """
    Test command blockdev_getsize64
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
    pv_name = params.get("pv_name")
    gf_result = gf.blockdev_getsize64(pv_name).stdout.strip()
    gf.close_session()

    get_size = int(gf_result)
    power = {'G': 1024 ** 3, "M": 1024 ** 2, "K": 1024}
    image_size = params["image_size"]
    image_size = int(image_size[:-1]) * power[image_size[-1]]
    logging.debug("Image size is %d" % image_size)
    logging.debug("Get size is %d" % get_size)

    if image_size != get_size:
        test.fail("Can not get the correct result.")


def test_blockdev_rereadpt(test, vm, params):
    """
    Test command blockdev-rereadpt
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        # add three disks here
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    pv_name = params.get("pv_name")
    gf.run()

    try:
        result = gf.blockdev_rereadpt(pv_name)
        if result.exit_status:
            test.fail("blockdev-rereadpt execute failed")
    finally:
        gf.close_session()


def test_canonical_device_name(test, vm, params):
    """
    Test command canonical_device_name
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
    pv_name = params.get("pv_name")

    gf_result = gf.list_filesystems().stdout.strip()
    partition_name = gf_result.split(":")[0]
    gf_result = gf.is_lv(partition_name).stdout.strip()
    if gf_result == "true":
        gf_result = []
        vg, lv = partition_name.split("/")[-2:]
        irregularity = '/dev/mapper/' + vg + '-' + lv
        gf_result.append(gf.canonical_device_name(irregularity).stdout.strip())
        logging.debug("canonical device name is %s" % gf_result)
    else:
        part = partition_name.split("/")
        gf_result = []
        for i in ['h', 'v']:
            part[-1] = i + part[-1][1:]
            irregularity = "/".join(part)
            s = gf.canonical_device_name(irregularity).stdout.strip()
            gf_result.append(s)
        logging.debug("canonical device name is %s" % gf_result)

    gf.close_session()

    for i in gf_result:
        if partition_name != i:
            test.fail("Can not get the correct result.")


def test_device_index(test, vm, params):
    """
    Test command device_index
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        # add three disks here
        gf.add_drive_opts(image_path, readonly=readonly)
        gf.add_drive_opts(image_path, readonly=readonly)
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    result = {'/dev/sda': '0', '/dev/sdb': '1', '/dev/sdc': '2'}
    try:
        for k, v in list(result.items()):
            index = gf.device_index(k).stdout.strip()
            if index != v:
                test.fail("Can not get the correct result.")
    finally:
        gf.close_session()


def test_list_devices(test, vm, params):
    """
    Test command list-devices
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
        # add three disks here
        gf.add_drive_opts(image_path, readonly=readonly)
        gf.add_drive_opts(image_path, readonly=readonly)
        gf.add_drive_opts(image_path, readonly=readonly)
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)
    gf.run()

    result = ['/dev/sda', '/dev/sdb', '/dev/sdc']
    try:
        devices = gf.list_devices().stdout.strip()
        logging.debug("%s" % devices)
    finally:
        gf.close_session()

    for i in result:
        if i not in devices:
            test.fail("Can not list device %s" % i)


def test_disk_format(test, vm, params):
    """
    Test command disk-format
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

    image_dir = params.get("img_dir", data_dir.get_tmp_dir())
    image_name = params.get('image_name')
    image_format = params["image_format"]
    params['image_name'] = 'test'
    support_format = ['raw', 'cow', 'qcow', 'qcow2', 'vdi', 'vmdk', 'vpc']

    for i in support_format:
        params['image_format'] = i
        image = qemu_storage.QemuImg(params, image_dir, '')
        image_path, _ = image.create(params)
        result = gf.disk_format(image_path).stdout.strip()
        os.remove(image_path)

        if result != i:
            gf.close_session()
            test.fail("Format is %s, expected is %s" % (result, i))

    gf.close_session()
    params['image_name'] = image_name
    params["image_format"] = image_format


def test_max_disks(test, vm, params):
    """
    Test command max-disks
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
    max_disk = gf.max_disks().stdout.strip()
    gf.close_session()

    logging.debug("The maximum number of disks is %s" % max_disk)

    if max_disk != '255':
        test.fail("The maximum number of disks is uncorrect")


def test_nr_devices(test, vm, params):
    """
    Test command nr-devices
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
    device_num = gf.nr_devices().stdout.strip()
    gf.close_session()

    logging.debug("The number of device is %s" % device_num)

    if device_num != '1':
        test.fail("The number of device is uncorrect")


def test_list_partitions(test, vm, params):
    """
    Test command list-partitions
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

    result = gf.list_partitions()
    devices = result.stdout.strip()

    if result.exit_status:
        gf.close_session()
        test.fail("Command list-partitions execute failed!")

    gf.close_session()

    if params["partition_type"] == "physical":
        logging.debug("Find devices %s" % devices)
        device_num = len(re.findall('\S+', devices))
        if device_num != 1:
            # add one disk with one partition as default
            test.fail("The number of device is uncorrect")
    elif params["partition_type"] == "lvm":
        logging.debug("This does not return logical volumes. "
                      "For that you will need to call 'lvs'")


def test_disk_has_backing_file(test, vm, params):
    """
    Test disk-has-backing-file
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

    image_dir = params.get("img_dir", data_dir.get_tmp_dir())
    image_name = params.get('image_name')
    image_format = params["image_format"]
    image_path = params.get("image_path")

    result = gf.disk_has_backing_file(image_path).stdout.strip()
    if result != "false":
        gf.close_session()
        test.fail("The disk has no backing file")

    params["image_format"] = "qcow2"
    params['image_name'] = "backfile"
    image = qemu_storage.QemuImg(params, image_dir, image_name)
    image.base_tag = True
    image.base_image_filename = image_path
    image.base_format = None
    image_path, _ = image.create(params)

    result = gf.disk_has_backing_file(image_path).stdout.strip()
    if result != "true":
        gf.close_session()
        test.fail("The disk has backing file")

    params["image_format"] = image_format
    params['image_name'] = image_name

    gf.close_session()


def test_disk_virtual_size(test, vm, params):
    """
    Test disk-virtual-size
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

    image_path = params.get("image_path")
    disk_size = int(os.path.getsize(image_path))
    result = int(gf.disk_virtual_size(image_path).stdout.strip())

    power = {'G': 1024 ** 3, "M": 1024 ** 2, "K": 1024}
    image_size = params["image_size"]
    image_size = int(image_size[:-1]) * power[image_size[-1]]

    if params["image_format"] == "raw":
        if result != disk_size and result != image_size:
            gf.close_session()
            test.fail("disk-virtual-size can't get uncorrect size")
    elif params["image_format"] == "qcow2":
        # disk size of qcow format is not fixed
        if result != image_size:
            gf.close_session()
            test.fail("disk-virtual-size can't get uncorrect size")

    gf.close_session()


def test_scrub_device(test, vm, params):
    """
    Test command scrub-device
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

    # create other disk has small size
    image_dir = params.get("img_dir", data_dir.get_tmp_dir())
    image_name = params.get("image_name")
    image_format = params["image_format"]
    image_size = params["image_size"]
    params['image_name'] = "scrub_test"
    params['image_size'] = "1G"
    image = qemu_storage.QemuImg(params, image_dir, '')
    image_path, _ = image.create(params)

    gf.add_drive_opts(image_path, readonly=readonly)
    gf.run()

    pv_name = "/dev/sdb"
    part_name = "/dev/sdb1"

    gf.part_disk(pv_name, 'msdos')
    gf.mkfs('ext4', part_name)

    device_info = gf.file(pv_name).stdout.strip()
    logging.debug(device_info)
    part_info = gf.file(part_name).stdout.strip()
    logging.debug(part_info)

    if len(device_info) < 20 and len(part_info) < 20:
        gf.close_session()
        test.fail("Info is not correct")

    # scrub partition, device info should be kept
    gf.scrub_device(part_name)

    device_info_new = gf.file(pv_name).stdout.strip()
    logging.debug(device_info_new)
    if device_info_new != device_info:
        gf.close_session()
        test.fail("Device info should be kept")

    part_info_new = gf.file(part_name).stdout.strip()
    logging.debug(part_info_new)
    if part_info_new != "data":
        gf.close_session()
        test.fail("Scrub partition failed")

    # scrub the whole device
    gf.scrub_device(pv_name)

    device_info_new = gf.file(pv_name).stdout.strip()
    logging.debug(device_info_new)
    if device_info_new != 'data':
        gf.close_session()
        test.fail("Scrub device failed")

    gf.close_session()
    params['image_name'] = image_name
    params['image_size'] = image_size


def test_scrub_file(test, vm, params):
    """
    Test command scrub-file
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

    file_path = "/test.log"
    content = "hello world"
    gf.write(file_path, content).stdout.strip()
    ret = gf.cat(file_path).stdout.strip()
    if ret != content:
        gf.close_session()
        test.fail("File content is not correct")

    gf.scrub_file(file_path)
    ret = gf.exists(file_path).stdout.strip()
    if ret != "false":
        gf.close_session()
        test.fail("scrub file failed")

    gf.close_session()


def test_scrub_freespace(test, vm, params):
    """
    Test command scrub-device
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

    # create other disk has small size
    image_dir = params.get("img_dir", data_dir.get_tmp_dir())
    image_name = params.get("image_name")
    image_format = params["image_format"]
    image_size = params["image_size"]
    params['image_name'] = "scrub_test"
    params['image_size'] = "1G"
    image = qemu_storage.QemuImg(params, image_dir, '')
    image_path, _ = image.create(params)

    gf.add_drive_opts(image_path, readonly=readonly)
    gf.run()

    pv_name = "/dev/sdb"
    part_name = "/dev/sdb1"

    gf.part_disk(pv_name, 'msdos')
    gf.mkfs('ext4', part_name)
    gf.mount(part_name, '/')

    dir_path = '/scrub'

    result = gf.scrub_freespace(dir_path)
    if result.exit_status:
        gf.close_session()
        test.fail("scrub_freespace failed.")

    result = gf.exists(dir_path).stdout.strip()
    if result != "false":
        gf.close_session()
        test.fail("folder still exist, scrub_freespace failed.")

    gf.close_session()
    params['image_name'] = image_name
    params['image_size'] = image_size


def test_md_test(test, vm, params):
    """
    Test command scrub-device
    """
    add_ref = params.get("gf_add_ref", "disk")
    readonly = "yes" == params.get("gf_add_readonly")

    gf = utils_test.libguestfs.GuestfishTools(params)
    if add_ref == "disk":
        image_path = params.get("image_path")
    elif add_ref == "domain":
        vm_name = params.get("main_vm")
        gf.add_domain(vm_name, readonly=readonly)

    image_dir = params.get("img_dir", data_dir.get_tmp_dir())
    image_name = params.get("image_name")
    image_format = params["image_format"]
    image_size = params["image_size"]

    params['image_size'] = "1G"
    for name in ['md1', 'md2', 'md3']:
        params['image_name'] = name
        image = qemu_storage.QemuImg(params, image_dir, '')
        image_path, _ = image.create(params)
        gf.add_drive_opts(image_path, readonly=readonly)

    gf.run()

    md = gf.list_md_devices().stdout.strip()
    if md:
        # close the existed md device
        gf.md_stop(md)

    device = "'/dev/sda /dev/sdb /dev/sdc'"
    gf.md_create("md11", device, missingbitmap="0x4",
                 chunk=8192, level="raid6")
    md = gf.list_md_devices().stdout.strip()
    logging.debug(md)
    if not md:
        gf.close_session()
        test.fail("Can not find the md device")

    detail = gf.md_detail(md).stdout.strip()
    logging.debug(detail)
    if "level: raid6" not in detail or "devname: md11" not in detail:
        gf.close_session()
        test.fail("MD detail info is not correct")

    gf.md_stop(md)

    gf.md_create("md21", device, nrdevices=2, spare=1,
                 chunk=8192, level="raid4")
    md = gf.list_md_devices().stdout.strip()
    logging.debug(md)
    if not md:
        gf.close_session()
        test.fail("Can not find the md device")
    stat = gf.md_stat(md).stdout.strip()
    logging.debug(stat)
    for i in re.findall("\w+", device):
        if i not in stat:
            gf.close_session()
            test.fail("MD stat is not correct")
    if "mdstat_flags: S" not in stat:
        gf.close_session()
        test.fail("There should be a S flag for spare disk")

    gf.close_session()

    params["image_name"] = image_name
    params["image_size"] = image_size


def test_part_add(test, vm, params):
    """
    Test command part-add
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

    image_dir = params.get("img_dir", data_dir.get_tmp_dir())
    image_name = params.get("image_name")
    image_size = params["image_size"]

    params['image_size'] = "1G"
    params['image_name'] = "disk"
    image = qemu_storage.QemuImg(params, image_dir, '')
    image_path, _ = image.create(params)
    gf.add_drive_opts(image_path, readonly=readonly)

    gf.run()

    # operate to the second disk
    partname = '/dev/sdb'
    gf.part_init(partname, 'msdos')
    gf.part_add(partname, 'p', 2, 100000)
    gf.part_add(partname, 'p', 100001, 200000)
    gf.part_add(partname, 'e', 200001, 300000)
    gf.part_add(partname, 'l', 240000, 280000)
    result = gf.part_list(partname).stdout.strip()

    partnum = result.count("part_num")
    if partnum != 4:
        gf.close_session()
        test.fail("partnum is %s, expect is 4" % partnum)

    gf.close_session()

    params["image_name"] = image_name
    params["image_size"] = image_size


def test_part_del(test, vm, params):
    """
    Test command part-del
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

    image_dir = params.get("img_dir", data_dir.get_tmp_dir())
    image_name = params.get("image_name")
    image_size = params["image_size"]

    params['image_size'] = "1G"
    params['image_name'] = "disk"
    image = qemu_storage.QemuImg(params, image_dir, '')
    image_path, _ = image.create(params)
    gf.add_drive_opts(image_path, readonly=readonly)

    gf.run()

    # operate to the second disk
    partname = '/dev/sdb'
    gf.part_init(partname, 'msdos')
    gf.part_add(partname, 'p', 2, 100000)
    gf.part_add(partname, 'p', 100001, 200000)
    result = gf.part_list(partname).stdout.strip()

    partnum = result.count("part_num")
    if partnum != 2:
        gf.close_session()
        test.fail("partnum is %s, expect is 2" % partnum)

    gf.part_del(partname, 2)
    result = gf.part_list(partname).stdout.strip()

    partnum = result.count("part_num")
    if partnum != 1:
        gf.close_session()
        test.fail("partnum is %s, expect is 1" % partnum)

    params["image_name"] = image_name
    params["image_size"] = image_size
    gf.close_session()


def test_part_init(test, vm, params):
    """
    Test command part-init
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

    image_dir = params.get("img_dir", data_dir.get_tmp_dir())
    image_name = params.get("image_name")
    image_size = params["image_size"]

    params['image_size'] = "1G"
    params['image_name'] = "disk"
    image = qemu_storage.QemuImg(params, image_dir, '')
    image_path, _ = image.create(params)
    gf.add_drive_opts(image_path, readonly=readonly)

    gf.run()

    # operate to the second disk
    partname = '/dev/sdb'
    for i in ['mbr', 'msdos', 'gpt', 'efi']:
        gf.part_init(partname, i)
        gf.part_add(partname, 'p', 100, 100000)
        gf.part_add(partname, 'p', 100001, 200000)
        result = gf.part_list(partname).stdout.strip()

        partnum = result.count("part_num")
        if partnum != 2:
            gf.close_session()
            test.fail("partnum is %s, expect is 2" % partnum)

    params["image_name"] = image_name
    params["image_size"] = image_size
    gf.close_session()


def test_part_set_get_bootable(test, vm, params):
    """
    Test command part-set-bootable, part-get-bootable
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
    pv_name = params.get("pv_name")

    result = {'1': 'true', 'true': 'true', '0': 'false', 'false': 'false'}
    partnum = '1'
    if params['partition_types'] == "physical":
        for k, v in list(result.items()):
            gf.part_set_bootable(pv_name, partnum, k)
            ret = gf.part_get_bootable(pv_name, 1).stdout.strip()
            if ret != v:
                gf.close_session()
                test.fail("Get the uncorrect status")

    gf.close_session()


def test_part_set_get_mbr_id(test, vm, params):
    """
    Test command part-set-mbr-id, part-get-mbr-id
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
    pv_name = params.get("pv_name")

    if params['partition_types'] == "physical":
        result = {'0xB': '0xb', '0x7': '0x7', '100': '0x64'}
        for i in ['1', '2', '3', '4']:
            for k, v in list(result.items()):
                gf.part_set_mbr_id(pv_name, i, k)
                ret = gf.part_get_mbr_id(pv_name, i).stdout.strip()
                if ret != v:
                    gf.close_session()
                    test.fail("Get the uncorrect mbr id")

    gf.close_session()


def test_part_disk(test, vm, params):
    """
    Test command part-disk
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

    pv_name = params.get("pv_name")
    gf.run()

    for ptype in ['msdos', 'gpt']:
        gf.part_disk(pv_name, ptype)
        result = gf.part_list(pv_name).stdout.strip()
        num = re.findall(r'part_num: (\d)', result)

        if int(num[0]) != 1:
            gf.close_session()
            test.fail("partnum is %s, expect is 1" % num)

        result = gf.part_get_parttype(pv_name).stdout.strip()

        if result != ptype:
            gf.close_session()
            test.fail("partition type is %s, expect is %s" %
                      (result, ptype))

    gf.close_session()


def test_part_to_dev(test, vm, params):
    """
    Test command part-to-dev
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

    pv_name = params.get("pv_name")
    gf.run()

    for ptype in ['mbr', 'msdos', 'gpt', 'efi']:
        gf.part_disk(pv_name, ptype)
        partname = gf.list_partitions().stdout.strip()
        devname = gf.part_to_dev(partname).stdout.strip()

        if devname != pv_name:
            gf.close_session()
            test.fail("device name is %s, expect is %s" %
                      (devname, pv_name))

    gf.close_session()


def test_part_to_partnum(test, vm, params):
    """
    Test command part-to-partnum
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

    pv_name = params.get("pv_name")
    gf.run()

    if params["partition_type"] == "physical":
        for ptype in ['mbr', 'msdos', 'gpt', 'efi']:
            gf.part_disk(pv_name, ptype)
            partname = gf.list_partitions().stdout.strip()
            num = partname[-1]
            devnum = gf.part_to_partnum(partname).stdout.strip()

            if devnum != num:
                gf.close_session()
                test.fail("device num is %s, expect is %s" %
                          (devnum, num))

    gf.close_session()


def test_sfdisk(test, vm, params):
    """
    Test command sfdisk
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

    pv_name = params.get("pv_name")
    gf.run()

    # "start,end,type"
    partition = ["1,100,83", "102,60,83", "200,50,83",
                 "251,200,5", "251,50,83"]
    lines = '"' + " ".join(partition) + '"'

    expect = ["1,100,83", "102,60,83", "200,50,83",
              "251,200,5", "251+,50-,83"]

    gf.part_init(pv_name, 'msdos')
    gf.sfdisk(pv_name, 0, 0, 0, lines)
    ret = gf.sfdisk_l(pv_name).stdout.strip()

    result = []
    for i in ret.split('\n'):
        if re.search("/dev/sda\d", i):
            l = re.findall("\S+", i)
            result.append("%s,%s,%s" % (l[1], l[3], l[5]))

    if result != expect:
        gf.close_session()
        logging.debug("Got result:\n %s", result)
        logging.debug("Expect result:\n %s", expect)
        test.fail("result is not match expect")

    gf.close_session()


def test_sfdiskM(test, vm, params):
    """
    Test command sfdiskM
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

    pv_name = params.get("pv_name")
    gf.run()

    if params["partition_type"] == "physical":
        # "start,end,type"
        partition = ["1,20,83", "22,30,83", "55,20,83", "76,20,5", "251,50,83"]
        lines = '"' + " ".join(partition) + '"'

        gf.sfdiskM(pv_name, lines)
        result = gf.sfdisk_l(pv_name).stdout.strip()
        logging.debug(result)

        l = re.findall("/dev/sda[0-9]", result)
        if len(l) != 4:
            gf.close_session()
            test.fail("result is not match expect")

        gf.close_session()


def test_sfdisk_N(test, vm, params):
    """
    Test command sfdisk-N
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

    pv_name = params.get("pv_name")
    gf.run()

    if params["partition_type"] == "physical":
        # "start,end,type"
        lines = ["1,100,83", "102,60,83", "200,50,83", "251,200,5"]

        for i, v in enumerate(lines):
            gf.sfdisk_N(pv_name, i+1, 0, 0, 0, v)

        ret = gf.sfdisk_l(pv_name).stdout.strip()

        result = []
        for i in ret.split('\n'):
            if re.search("/dev/sda\d", i):
                l = re.findall("\S+", i)
                result.append("%s,%s,%s" % (l[1], l[3], l[5]))

        logging.debug(lines)
        logging.debug(result)
        if result != lines:
            gf.close_session()
            test.fail("result is not match expect")

        gf.close_session()


def test_sfdisk_disk_geometry(test, vm, params):
    """
    Test command sfdisk-disk-geometry
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

    pv_name = params.get("pv_name")
    gf.run()

    gf.part_init(pv_name, 'msdos')
    result = gf.sfdisk_disk_geometry(pv_name).stdout.strip()

    if not result:
        gf.close_session()
        test.fail("result should not be empty")

    gf.close_session()


def test_sfdisk_kernel_geometry(test, vm, params):
    """
    Test command sfdisk-kernel-geometry
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

    pv_name = params.get("pv_name")
    gf.run()

    gf.part_init(pv_name, 'msdos')
    result = gf.sfdisk_kernel_geometry(pv_name).stdout.strip()

    if not result:
        gf.close_session()
        test.fail("result should not be empty")

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
            prepare_image(test, params)
            testcase(test, vm, params)
