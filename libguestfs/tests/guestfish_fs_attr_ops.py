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


def test_set_get_e2uuid(test, vm, params):
    """
    Test command set_e2uuid, get_e2uuid, vfs_uuid and findfs-uuid:
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

    mount_point = params.get("mount_point")
    # set_e2uuid
    temp, uuid = process.getstatusoutput("uuidgen")
    gf.set_e2uuid(mount_point, uuid)

    # get_e2uuid
    gf_result = gf.get_e2uuid(mount_point)
    if gf_result.stdout.split()[0] != uuid:
        gf.close_session()
        test.fail("set_get_e2uuid failed.")

    # vfs_uuid
    gf_result = gf.vfs_uuid(mount_point)
    if gf_result.stdout.split()[0] != uuid:
        gf.close_session()
        test.fail("set_get_e2uuid failed.")

    # find_uuid
    gf_result = gf.findfs_uuid(uuid)
    if gf_result.stdout.split()[0] != mount_point:
        gf.close_session()
        test.fail("set_get_e2uuid failed.")

    gf.close_session()


def test_set_uuid(test, vm, params):
    """
    Test command set_uuid
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

    mount_point = params.get("mount_point")

    # set_uuid
    temp, uuid = process.getstatusoutput("uuidgen")
    gf.set_uuid(mount_point, uuid)

    gf_result = gf.vfs_uuid(mount_point)
    if gf_result.stdout.split()[0] != uuid:
        gf.close_session()
        test.fail("set_uuid failed.")
    gf.close_session()


def test_set_get_e2label(test, vm, params):
    """
    Test command set_e2label, get_e2label, vfs_label and findfs_label
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

    mount_point = params.get("mount_point")

    # set_e2label
    label = 'TEST_LABEL'
    gf.set_e2label(mount_point, label)

    # get_e2label
    gf_result = gf.get_e2label(mount_point)
    if gf_result.stdout.split()[0] != label:
        gf.close_session()
        test.fail("set_get_e2label failed.")

    # vfs_label
    gf_result = gf.vfs_label(mount_point)
    if gf_result.stdout.split()[0] != label:
        gf.close_session()
        test.fail("set_get_e2label failed.")

    # findfs_label
    gf_result = gf.findfs_label(label)
    if gf_result.stdout.split()[0] != mount_point:
        gf.close_session()
        test.fail("set_get_e2label failed.")

    gf.close_session()


def test_set_label(test, vm, params):
    """
    Test command set_label
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

    mount_point = params.get("mount_point")

    # set_label
    label = 'TEST_LABEL'
    gf.set_label(mount_point, label)

    gf_result = gf.vfs_label(mount_point)
    if gf_result.stdout.split()[0] != label:
        gf.close_session()
        test.fail("set_label failed.")
    gf.close_session()


def test_set_get_e2attrs(test, vm, params):
    """
    Test command set_e2attrs and get_e2attrs
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
    fstype = params.get("fs_type")
    gf.run()

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')
    # set_e2attrs 't'
    filename = '/testfile'
    gf.rm_rf(filename)
    gf.touch(filename)
    gf.set_e2attrs(filename, 't')
    # get_e2attrs
    expected_attrs = 't'
    if fstype == 'ext4':
        expected_attrs = 'et'
    gf_result = gf.get_e2attrs(filename)
    if gf_result.stdout.split()[0] != expected_attrs:
        gf.close_session()
        test.fail("set_get_e2attrs failed.")
    # set_e2attrs 'u'
    gf.set_e2attrs(filename, 'u')
    # get_e2attrs
    expected_attrs = 'tu'
    if fstype == 'ext4':
        expected_attrs = 'etu'
    gf_result = gf.get_e2attrs(filename)
    if gf_result.stdout.split()[0] != expected_attrs:
        gf.close_session()
        test.fail("set_get_e2attrs failed.")
    # set_e2attrs 'a', 'c'
    gf.set_e2attrs(filename, 'a')
    gf.set_e2attrs(filename, 'c')
    # get_e2attrs
    expected_attrs = 'actu'
    if fstype == 'ext4':
        expected_attrs = 'acetu'
    gf_result = gf.get_e2attrs(filename)
    if gf_result.stdout.split()[0] != expected_attrs:
        gf.close_session()
        test.fail("set_get_e2attrs failed.")
    # set_e2attrs 'c' clear:true
    gf.set_e2attrs(filename, 'c', 'true')
    # get_e2attrs
    expected_attrs = 'atu'
    if fstype == 'ext4':
        expected_attrs = 'aetu'
    gf_result = gf.get_e2attrs(filename)
    if gf_result.stdout.split()[0] != expected_attrs:
        gf.close_session()
        test.fail("set_get_e2attrs failed.")

    # set_e2attrs 't' clear:false
    gf.set_e2attrs(filename, 't', 'false')
    # get_e2attrs
    gf_result = gf.get_e2attrs(filename)
    if gf_result.stdout.split()[0] != expected_attrs:
        gf.close_session()
        test.fail("set_get_e2attrs failed.")
    gf.close_session()


def test_set_get_e2generation(test, vm, params):
    """
    Test command set_e2generation and get_e2generation
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

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    # set_e2generation
    filename = '/testfile'
    generation = '1231231'
    gf.rm_rf(filename)
    gf.touch(filename)
    gf.set_e2generation(filename, generation)
    # get_e2generation
    gf_result = gf.get_e2generation(filename)
    if gf_result.stdout.split()[0] != generation:
        gf.close_session()
        test.fail("set_get_e2generation failed.")
    gf.close_session()


def test_statvfs(test, vm, params):
    """
    Test command statvfs
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

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    # statvfs
    gf_result = gf.statvfs('/')
    List = gf_result.stdout.split()
    atti_list = ['bsize:', 'frsize:', 'blocks:', 'bfree:', 'bavail:',
                 'files:', 'ffree:', 'favail:', 'fsid:', 'flag:', 'namemax:']
    for atti in atti_list:
        if atti not in List:
            gf.close_session()
            test.fail("statvfs failed.")

    # don't do further free-inodes test for vfat/ntfs as they behave
    # very different from ext2/3/4.
    fs_type = gf.vfs_type(mount_point).stdout.split()[0]
    if fs_type == 'vfat' or fs_type == 'ntfs':
        gf.close_session()
        return

    # Create a new file within the file system, and check if
    # the related value has changed accordingly. We choose
    # ffree (free inodes) to be checked.
    ffree_index = List.index('ffree:')
    ffree_value = int(List[ffree_index + 1])

    gf.touch('/testfile')
    List = gf.statvfs('/').stdout.split()
    ffree_index = List.index('ffree:')
    new_ffree_value = int(List[ffree_index + 1])
    if new_ffree_value != (ffree_value - 1):
        gf.close_session()
        test.fail("statvfs failed.")

    gf.close_session()


def test_tune2fs_l(test, vm, params):
    """
    Test command tune2fs_l
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

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    # statvfs
    List = gf.statvfs('/').stdout.split()
    inodes_statvfs_index = List.index('files:')
    inodes_statvfs = List[inodes_statvfs_index + 1]
    inodes_tune2fs_str = 'Inode count: %s' % inodes_statvfs
    # tune2fs_l
    gf_result = gf.tune2fs_l(mount_point)

    if inodes_tune2fs_str not in gf_result.stdout:
        gf.close_session()
        test.fail("tune2fs_l failed.")

    gf.close_session()


def test_tune2fs(test, vm, params):
    """
    Test command tune2fs
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

    mount_point = params.get("mount_point")

    # tune2fs: ajust
    newmaxmc, newmc, newgroup, newintervalbc, newreservebc,\
        newuser, neweb = '3', '5', '1', '600', '600', '1', 'panic'
    gf.tune2fs(mount_point, 'true', newmaxmc, newmc, neweb,
               newgroup, newintervalbc, '30', '/',
               newreservebc, '1')

    # tune2fs_l: check
    str_list = ['Mount count: ' + newmc,
                'Maximum mount count: ' + newmaxmc,
                'Errors behavior: ' + 'Panic',
                'Reserved blocks gid: ' + newgroup,
                'Check interval: ' + newintervalbc,
                'Reserved block count: ' + newreservebc,
                'Reserved blocks uid: ' + newuser]

    gf_result = gf.tune2fs_l(mount_point)
    for expect_str in str_list:
        if expect_str not in gf_result.stdout:
            gf.close_session()
            test.fail("tune2fs failed.")

    gf.close_session()


def test_checkfs(test, vm, params):
    """
    Test command vfs_type, fsck
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

    mount_point = params.get("mount_point")

    # vfs_type
    type_list = ['ext2', 'ext3', 'ext4', 'ntfs', 'vfat', 'xfs']
    gf_result = gf.vfs_type(mount_point)
    if gf_result.stdout.split()[0] not in type_list:
        gf.close_session()
        test.fail("checkfs failed.")

    # fsck
    fsck_result = gf.fsck(gf_result.stdout.split()[0], mount_point)
    logging.debug('test_checkfs: [fsck: %s %s], the result is [%s]' %
                  (gf_result.stdout.split()[0], mount_point,
                   fsck_result.stdout.split()[0]))
    gf.close_session()


def test_mkfs_opts(test, vm, params):
    """
    Test command mkfs/mkfs_opts
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
    fstype = params.get("fs_type")
    partition_type = params.get("partition_type")

    test_format = params.get("image_format")
    test_size = params.get("image_size")
    test_dir = params.get("img_dir", data_dir.get_tmp_dir())
    test_img = test_dir + '/test_resize2fs.img'
    test_pv = '/dev/sdb'
    os.system('qemu-img create -f ' + test_format + ' ' +
              test_img + ' ' + test_size + ' > /dev/null')
    gf.add_drive(test_img)
    gf.run()

    # create pv and vg
    if partition_type == "lvm":
        test_vg = "mkfs_testvg"
        test_lv = "mkfs_testlv"
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

    size_list = ['1024', '2048']
    # blocksize cannot be set on btrfs
    if fstype == 'btrfs':
        size_list = ['']
    if fstype == 'vfat':
        size_list = ['2048', '4096']
    for blocksize in size_list:
        # mkfs-opts/mkfs
        aaa = gf.mkfs_opts(fstype, test_mountpoint, blocksize)
        # check type
        vfs_type_result = gf.vfs_type(test_mountpoint).stdout.split()[0]
        if fstype != vfs_type_result:
            os.system('rm -f ' + test_img + ' > /dev/null')
            gf.close_session()
            test.fail("test_mkfs-opts failed.")
        # check blocksize
        if fstype == 'btrfs':
            os.system('rm -f ' + test_img + ' > /dev/null')
            gf.close_session()
            return

        gf.mount(test_mountpoint, '/')
        blocksize_str = 'bsize: %s' % blocksize
        statvfs_result = gf.statvfs('/')
        if blocksize_str not in statvfs_result.stdout:
            gf.close_session()
            os.system('rm -f ' + test_img + ' > /dev/null')
            test.fail("test_mkfs_opts failed.")

        gf.umount('/')
    os.system('rm -f ' + test_img + ' > /dev/null')
    gf.close_session()


def test_blkid(test, vm, params):
    """
    Test command blkid
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

    mount_point = params.get("mount_point")

    # blkid
    blkid_result = gf.blkid(mount_point)
    # check all values are not null
    for pair in blkid_result.stdout.strip().split('\n'):
        key_value = pair.strip().split(':')
        if len(key_value) < 2:
            gf.close_session()
            test.fail("blkid failed.")
        key, value = key_value[0].strip(), key_value[1].strip()
        if key == '' or value == '':
            gf.close_session()
            test.fail("blkid failed.")
    # check filesystem output by blkid is correct

    fstype = gf.vfs_type(mount_point).stdout.split()[0]
    fstype_str = 'TYPE: %s' % fstype
    if fstype not in blkid_result.stdout:
        gf.close_session()
        test.fail("blkid failed.")
    gf.close_session()


def test_filesystem_available(test, vm, params):
    """
    Test command filesystem_available
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

    mount_point = params.get("mount_point")

    fs_dic = {'ext2': 'true', 'ext3': 'true', 'ext4': 'true', 'xfs': 'true',
              'btrfs': 'true', 'msdos': 'true', 'ntfs': 'false',
              'unknown': 'false'}
    for key, value in list(fs_dic.items()):
        gf_result = gf.filesystem_available(key)
        if gf_result.stdout.split()[0] != value:
            gf.close_session()
            test.fail("test_filesystem_available failed.")

    gf.close_session()


def test_e2fsck(test, vm, params):
    """
    Test command e2fsck
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

    mount_point = params.get("mount_point")

    # e2fsck
    gf.e2fsck(mount_point, 'yes')
    # check if everything is ok
    gf_result = gf.mount(mount_point, '/')
    if gf_result.exit_status != 0:
        gf.close_session()
        test.fail("test_e2fsck failed.")
    gf.close_session()


def test_list_filesystems(test, vm, params):
    """
    Test command list-filesystems
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

    mount_point = params.get("mount_point")

    # list_filesystems
    gf_result = gf.list_filesystems()
    # check mount_point
    fstype = gf.vfs_type(mount_point).stdout.split()[0]
    fstype_str = '%s: %s' % (mount_point, fstype)
    if fstype_str not in gf_result.stdout:
        gf.close_session()
        test.fail("test_list_filesystems failed.")
    gf.close_session()


def test_mkfifo(test, vm, params):
    """
    Test command test_mkfifo
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

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    # mkfifo
    gf.mkfifo('511', '/node')
    # check
    gf_result = gf.stat('/node')
    mode_str = 'mode: 4589'
    if mode_str not in gf_result.stdout:
        gf.close_session()
        test.fail("test_mkfifo failed.")
    gf.close_session()


def test_sync(test, vm, params):
    """
    Test command sync
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

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    # sync
    gf_result = gf.sync()
    if gf_result.exit_status != 0:
        gf.close_session()
        test.fail("test_sync failed.")
    gf.close_session()


def test_mklost_and_found(test, vm, params):
    """
    Test command mklost_and_found
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

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    lost_and_found_file = '/lost+found'
    gf.rm_rf(lost_and_found_file)
    gf.mklost_and_found('/')
    ll_result = gf.ll(lost_and_found_file)
    if ll_result.exit_status != 0:
        gf.close_session()
        test.fail("test_mklost_and_found failed.")
    gf.close_session()


def test_mknod(test, vm, params):
    """
    Test command mknod
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

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    nodename = '/testnode'
    gf.rm_rf(nodename)

    # mknod
    for node in ['file', 'block', 'char', 'fifo']:
        if node == "block":
            mode = "0060777"
            major = "8"
            minor = "1"
            permission = "brwxr-xr-x"
        elif node == "char":
            mode = "0020777"
            major = "4"
            minor = "1"
            permission = "crwxr-xr-x"
        elif node == "file":
            mode = "0100777"
            major = "5"
            minor = "1"
            permission = "-rwxr-xr-x"
        elif node == "fifo":
            mode = "0010777"
            major = "5"
            minor = "1"
            permission = "prwxr-xr-x"
        gf.mknod(mode, major, minor, nodename)
        ll_result = gf.ll(nodename)
        if permission not in ll_result.stdout:
            gf.close_session()
            test.fail("test_mknod failed.")
        gf.rm_rf(nodename)
    gf.close_session()


def test_mknod_b(test, vm, params):
    """
    Test command mknod_b
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

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    nodename = '/testnode'
    gf.rm_rf(nodename)
    gf.umask('022')

    # mknod_b
    mode = "0060777"
    major = "8"
    minor = "1"
    permission = "brwxr-xr-x"

    gf.mknod_b(mode, major, minor, nodename)
    ll_result = gf.ll(nodename)
    if permission not in ll_result.stdout:
        gf.close_session()
        test.fail("test_mknod_b failed.")
    gf.rm_rf(nodename)
    gf.close_session()


def test_mknod_c(test, vm, params):
    """
    Test command mknod_c
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

    mount_point = params.get("mount_point")
    gf.mount(mount_point, '/')

    nodename = '/testnode'
    gf.rm_rf(nodename)
    gf.umask('022')

    # mknod_b
    mode = "0060777"
    major = "8"
    minor = "1"
    permission = "brwxr-xr-x"

    gf.mknod_c(mode, major, minor, nodename)
    ll_result = gf.ll(nodename)
    if permission not in ll_result.stdout:
        gf.close_session()
        test.fail("test_mknod_c failed.")
    gf.rm_rf(nodename)
    gf.close_session()


def test_ntfsresize_opts(test, vm, params):
    """
    Test command ntfsresize_opts
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

    mount_point = params.get("mount_point")

    gf.mkfs('ntfs', mount_point)
    gf.mount(mount_point, '/')

    # statvfs
    statvfs_result = gf.statvfs('/')
    List = statvfs_result.stdout.split()
    bsize_index = List.index('bsize:')
    blocks_index = List.index('blocks:')
    bsize = int(List[bsize_index + 1])
    blocks = int(List[blocks_index + 1])

    expected_blocks = blocks / 2
    new_size = expected_blocks * bsize

    # ntfsresize_opts: resize
    gf.umount('/')
    gf.ntfsresize_opts(mount_point, new_size, 'true')

    # statvfs: check
    gf.mount(mount_point, '/')
    statvfs_result = gf.statvfs('/')
    List = statvfs_result.stdout.split()
    new_blocks_index = List.index('blocks:')
    new_blocks = int(List[new_blocks_index + 1])
    if abs(new_blocks - expected_blocks) >= 2:
        gf.close_session()
        test.fail("test_ntfsresize_opts failed.")

    # ntfsresize_opts: expand
    gf.umount('/')
    gf.ntfsresize_opts(mount_point, None, 'true')
    gf.mount(mount_point, '/')
    statvfs_result = gf.statvfs('/')
    List = statvfs_result.stdout.split()
    new_blocks_index = List.index('blocks:')
    new_blocks = int(List[new_blocks_index + 1])
    if abs(new_blocks - blocks) >= 2:
        gf.close_session()
        test.fail("test_ntfsresize_opts failed.")

    gf.close_session()


def test_resize2fs(test, vm, params):
    """
    Test command resize2fs
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

    mount_point = params.get("mount_point")
    image_size = params.get("image_size")
    image_format = params.get("image_format")

    last_char = image_size[len(image_size)-1]
    if str.isdigit(last_char):
        altsize_int = int(image_size[0: len(image_size)]) * 2
        altsize = str(altsize_int)
    else:
        altsize_int = int(image_size[0: len(image_size)-1]) * 2
        altsize = str(altsize_int) + image_size[len(image_size)-1]
    tmpdir = '/tmp/autotest-libguestfs-resize2fs'
    altimg = tmpdir + '/test_resize2fs.img'
    altdev = '/dev/sdb'
    os.system('mkdir -p ' + tmpdir)
    os.system('qemu-img create -f ' + image_format + ' ' +
              altimg + ' ' + altsize + ' > /dev/null')

    gf.add_drive(altimg)
    gf.run()
    gf.dd(mount_point, altdev)
    gf.e2fsck(altdev)
    gf.resize2fs(altdev)
    gf.mount(altdev, '/')
    gf.umount_all()

    t_result = gf.tune2fs_l(altdev)
    b_result = gf.blockdev_getsize64(altdev)

    for items in t_result.stdout.split('\n'):
        if 'Block count' in items:
            block_count = int(items.split(':')[1].strip())
        if 'Block size' in items:
            block_size = int(items.split(':')[1].strip())
    getsize64 = int(b_result.stdout.split()[0])
    os.system('rm -rf %s ' % tmpdir)
    if block_count * block_size <= getsize64 * 0.99:
        gf.close_session()
        test.fail("test_resize2fs failed, device size: %s, block_count: %s\
                             block_size: %s" % (getsize64, block_count, block_size))
    gf.close_session()


def test_resize2fs_M(test, vm, params):
    """
    Test command resize2fs_M
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

    mount_point = params.get("mount_point")
    image_size = params.get("image_size")
    image_format = params.get("image_format")
    last_char = image_size[len(image_size)-1]
    if str.isdigit(last_char):
        altsize_int = int(image_size[0: len(image_size)]) * 2
        altsize = str(altsize_int)
    else:
        altsize_int = int(image_size[0: len(image_size)-1]) * 2
        altsize = str(altsize_int) + image_size[len(image_size)-1]
    tmpdir = '/tmp/autotest-libguestfs-resize2fs'
    altimg = tmpdir + '/test_resize2fs_M.img'
    altdev = '/dev/sdb'
    os.system('mkdir -p ' + tmpdir)
    os.system('qemu-img create -f ' + image_format + ' ' +
              altimg + ' ' + altsize + ' > /dev/null')

    gf.add_drive(altimg)
    gf.run()
    gf.dd(mount_point, altdev)
    e2_result = gf.e2fsck(altdev, None, 'true')
    resize_result = gf.resize2fs_M(altdev)
    mount_result = gf.mount(altdev, '/')

    os.system('rm -rf %s ' % tmpdir)

    if mount_result.exit_status != 0 and resize_result.exit_status != 0:
        gf.close_session()
        test.fail("test_resize2fs_M failed.")
    gf.close_session()


def test_resize2fs_size(test, vm, params):
    """
    Test command resize2fs_size
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

    mount_point = params.get("mount_point")
    gf.run()

    t_result = gf.tune2fs_l(mount_point)
    for items in t_result.stdout.split('\n'):
        if 'Block count' in items:
            block_count = int(items.split(':')[1].strip())
        if 'Block size' in items:
            block_size = int(items.split(':')[1].strip())
    filesystem_size = block_size / 2 * block_count
    gf.e2fsck_f(mount_point)
    gf.resize2fs_size(mount_point, filesystem_size)
    new_t_result = gf.tune2fs_l(mount_point)
    for items in new_t_result.stdout.split('\n'):
        if 'Block count' in items:
            new_block_count = int(items.split(':')[1].strip())
    expected_blockcount = block_count / 2
    if expected_blockcount != new_block_count:
        gf.close_session()
        test.fail("test_resize2fs_size failed.")
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
