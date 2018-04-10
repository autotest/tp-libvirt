import re
import os
import logging
import aexpect

from avocado.utils import process

from virttest import virt_vm
from virttest import data_dir
from virttest import remote
from virttest import utils_test
from virttest import utils_misc


def test_created_volume_group(test, vm, params):
    """
    1) Do some necessary check
    2) Format additional disk with new volume
    3) Login to check created volume group
    """
    add_device = params.get("gf_additional_device", "/dev/vdb")
    device_in_lgf = process.run("echo %s | sed -e 's/vd/sd/g'" % add_device,
                                ignore_status=True, shell=True).stdout_text.strip()

    device_part = "%s1" % device_in_lgf
    # Mount specific partition
    params['special_mountpoints'] = [device_part]
    vt = utils_test.libguestfs.VirtTools(vm, params)
    # Create a new vm with additional disk
    vt.update_vm_disk()
    # Default vm_ref is oldvm, so switch it.
    vm_ref = vt.newvm.name

    # Format disk
    volume_name = "/dev/VGTest/LVTest"
    format_result = vt.format_disk(filesystem="ext3", lvm=volume_name)
    if format_result.exit_status:
        test.fail("Format added disk failed.")
    logging.info("Format added disk successfully.")

    # List filesystems detail
    list_fs_detail = vt.get_filesystems_info(vm_ref)
    if list_fs_detail.exit_status:
        test.fail("List filesystems detail failed.")
    logging.info("List filesystems detail successfully.")

    attached_vm = vt.newvm
    try:
        attached_vm.start()
        session = attached_vm.wait_for_login()
        if session.cmd_status("which vgs"):
            attached_vm.destroy()
            attached_vm.wait_for_shutdown()
            logging.error("Can not use volume group in guest, SKIP...")
            return
    except (virt_vm.VMError, remote.LoginError) as detail:
        attached_vm.destroy()
        test.fail(str(detail))

    try:
        output = session.cmd_output("vgs --all", timeout=5)
        logging.debug(output)
        attached_vm.destroy()
        attached_vm.wait_for_shutdown()
    except (virt_vm.VMError, remote.LoginError, aexpect.ShellError) as detail:
        logging.error(str(detail))
        if attached_vm.is_alive():
            attached_vm.destroy()
    if not re.search("VGTest", output):
        test.fail("Can not find created volume group in vm.")
    logging.info("Check created volume in vm successfully.")


def test_created_volume(test, vm, params):
    """
    1) Do some necessary check
    2) Format additional disk with new volume
    3) Login to check created volume
    """
    add_device = params.get("gf_additional_device", "/dev/vdb")
    device_in_lgf = process.run("echo %s | sed -e 's/vd/sd/g'" % add_device,
                                ignore_status=True, shell=True).stdout_text.strip()
    if utils_test.libguestfs.primary_disk_virtio(vm):
        device_in_vm = add_device
    else:
        device_in_vm = "/dev/vda"

    device_part = "%s1" % device_in_lgf
    device_part_in_vm = "%s1" % device_in_vm
    # Mount specific partition
    params['special_mountpoints'] = [device_part]
    vt = utils_test.libguestfs.VirtTools(vm, params)
    # Create a new vm with additional disk
    vt.update_vm_disk()
    # Default vm_ref is oldvm, so switch it.
    vm_ref = vt.newvm.name

    # Format disk
    volume_name = "/dev/VGTest/LVTest"
    format_result = vt.format_disk(filesystem="ext3", lvm=volume_name)
    if format_result.exit_status:
        test.fail("Format added disk failed.")
    logging.info("Format added disk successfully.")

    # List filesystems detail
    list_fs_detail = vt.get_filesystems_info(vm_ref)
    if list_fs_detail.exit_status:
        test.fail("List filesystems detail failed.")
    logging.info("List filesystems detail successfully.")

    mountpoint = params.get("vt_mountpoint", "/mnt")
    path = os.path.join(mountpoint, "test_created_volume.img")

    params['special_mountpoints'] = [volume_name]
    mounts, mounto = vt.guestmount(mountpoint, vm_ref)
    if mounts is False:
        test.fail("Mount vm's filesystem failed.")
    else:
        dfs, dfo = process.getstatusoutput("df")
        logging.debug(dfo)
        if not re.search("/dev/fuse", dfo):
            utils_misc.umount("", mountpoint, "")
            test.fail("Did not find mounted filesystem.")

    dd_cmd = "dd if=/dev/zero of=%s bs=1M count=5" % path
    dds, ddo = process.getstatusoutput(dd_cmd)
    logging.debug(ddo)
    if dds:
        utils_misc.umount("", mountpoint, "")
        test.fail("Create a image file failed.")

    md5s, md5o = process.getstatusoutput("md5sum %s" % path)
    logging.debug(md5o)
    if md5s:
        utils_misc.umount("", mountpoint, "")
        test.fail("Get md5 value failed.")

    if not utils_misc.umount("", mountpoint, ""):
        test.fail("Unmount vm's filesytem failed.")
    logging.info("Unmount vm's filesystem successfully.")

    attached_vm = vt.newvm
    try:
        attached_vm.start()
        session = attached_vm.wait_for_login()
        if session.cmd_status("which lvs"):
            attached_vm.destroy()
            attached_vm.wait_for_shutdown()
            logging.error("Can not use volume in guest, SKIP...")
            return
    except (virt_vm.VMError, remote.LoginError) as detail:
        attached_vm.destroy()
        test.fail(str(detail))

    try:
        mounts = session.cmd_status("mount %s %s" % (volume_name, mountpoint),
                                    timeout=10)
        if mounts:
            logging.error("Mount volume failed.")
        md51 = session.cmd_output("md5sum %s" % path)
        logging.debug(md51)
        if not re.search(md5o, md51):
            test.fail("Got a different md5.")
        logging.info("Got matched md5.")
        attached_vm.destroy()
        attached_vm.wait_for_shutdown()
    except (virt_vm.VMError, remote.LoginError, aexpect.ShellError) as detail:
        logging.error(str(detail))
        if attached_vm.is_alive():
            attached_vm.destroy()
    logging.info("Check created image on guest successfully.")


def run(test, params, env):
    """
    Test volume operations with virt-format.
    """
    vm_name = params.get("main_vm")
    new_vm_name = params.get("gf_updated_new_vm")
    vm = env.get_vm(vm_name)

    # To make sure old vm is down
    if vm.is_alive():
        vm.destroy()

    operation = params.get("vt_volume_operation")
    testcase = globals()["test_%s" % operation]
    try:
        # Create a new vm for editing and easier cleanup :)
        utils_test.libguestfs.define_new_vm(vm_name, new_vm_name)
        testcase(test, vm, params)
    finally:
        disk_path = os.path.join(data_dir.get_tmp_dir(),
                                 params.get("gf_updated_target_dev"))
        utils_test.libguestfs.cleanup_vm(new_vm_name, disk_path)
