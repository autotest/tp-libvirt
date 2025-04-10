# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os.path

from virttest import virsh
from virttest import libvirt_version
from virttest import utils_disk
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base
from provider.snapshot import snapshot_base

virsh_dargs = {"debug": True, "ignore_status": False}


def write_file(vm, params, file_name, format_disk=False):
    """
    Write file to target disk

    :param vm: vm object.
    :param params: Dictionary with the test parameters.
    :param file_name: file name.
    :param format_disk: only format disk for one time in vm.
    """
    vm_status = params.get("vm_status")
    target_disk = params.get("target_disk")

    if vm_status == "vm_paused" and vm.state() != "running":
        virsh.resume(vm.name, **virsh_dargs)
    session = vm.wait_for_login()
    if format_disk:
        cmd = "mkfs.ext4 /dev/%s;mount /dev/%s /mnt" % (
            target_disk, target_disk)
        session.cmd_status_output(cmd)
    utils_disk.dd_data_to_vm_disk(session, file_name, bs='1M',
                                  count='2')
    session.close()


def check_after_deleting_snap(test, vm, params, expected_hash, del_snap):
    """
    Check two disks xml, guest file hash value, snapshot memory file.

    :param test: test object.
    :param vm: vm object.
    :param params: Dictionary with the test parameters.
    :param expected_hash: expected file hash value in guest.
    :param del_snap: the deleted snap order in snap names.
    """
    disk_type = params.get("disk_type")
    target_disk = params.get("target_disk")
    file_path = params.get("file_path")
    vm_status = params.get("vm_status")
    check_obj = check_functions.Checkfunction(test, vm, params)
    mem_file = params.get("mem_files")[int(del_snap)-1]
    expected_source_len = 2 if del_snap == "1" else 1
    if vm_status == "vm_paused" and vm.state() != "running":
        virsh.resume(vm.name, **virsh_dargs)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    new_disk_sources = disk_base.DiskBase.get_source_list(
        vmxml, disk_type, target_disk)

    if len(new_disk_sources) != expected_source_len:
        test.fail("New disk source list length should be %s, instead of %s " %
                  (new_disk_sources, expected_source_len))

    new_source = libvirt_disk.get_first_disk_source(vm)
    if params.get("original_disk_source") != new_source:
        test.fail("Disk source should not change to %s" % new_source)

    session = vm.wait_for_login()
    check_obj.check_hash_list([file_path], [expected_hash], session=session)
    session.close()

    if os.path.exists(mem_file):
        test.fail("The snap memory file %s is existed " % mem_file)
    test.log.debug("Checking disks xml, file hash value, and snapshot "
                   "memory file were successful.")


def run(test, params, env):
    """
    :params test: test object
    :params params: wrapped dict with all parameters
    :params env: test object
    """
    def run_test():
        """
        Verify snapshot-delete disk and memory snapshot.
        """
        test.log.info("TEST_STEP1: Attach a new disk to guest.")
        vm.start()
        vm.wait_for_login().close()
        new_disk, _ = disk_obj.prepare_disk_obj(disk_type, disk_dict)
        virsh.attach_device(vm_name, new_disk.xml, **virsh_dargs)

        test.log.info("TEST_STEP2: Mount new disk and write random data.")
        write_file(vm, params, file_name=file_path, format_disk=True)

        test.log.info("TEST_STEP3,4: Create the 1st snapshot with snapshot xml")
        if vm_status == "vm_paused":
            virsh.suspend(vm_name, **virsh_dargs)
        test_obj.create_snapshot_by_xml(
            eval(snapshot_dict % (snap_names[0], params.get("mem_files")[0])),
            eval(snapshot_disk_list % snap_file1))

        test.log.info("TEST_STEP5: Write random data to new disk again.")
        write_file(vm, params, file_name=file_path)

        test.log.info("TEST_STEP6,7:Create the 2nd snapshot with snapshot xml.")
        if vm_status == "vm_paused":
            virsh.suspend(vm_name, **virsh_dargs)
        test_obj.create_snapshot_by_xml(
            eval(snapshot_dict % (snap_names[1],  params.get("mem_files")[1])),
            eval(snapshot_disk_list % snap_file2))

        test.log.info("TEST_STEP8: Write random data to new disk again.")
        write_file(vm, params, file_name=file_path)
        expected_hash, _ = block_obj.get_hash_value(check_item=file_path)

        test.log.info("TEST_STEP9: Update vm status.")
        if vm_status == "vm_paused":
            virsh.suspend(vm_name, **virsh_dargs)

        test.log.info("TEST_STEP10: Delete the 1st snapshot and check xml.")
        virsh.snapshot_delete(vm_name, snap_names[0], **virsh_dargs)
        check_after_deleting_snap(test, vm, params, expected_hash, "1")

        test.log.info("TEST_STEP11: Delete the 2nd snapshot and check xml.")
        virsh.snapshot_delete(vm_name, snap_names[1], **virsh_dargs)
        check_after_deleting_snap(test, vm, params, expected_hash, "2")

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        snap_names.reverse()
        test_obj.teardown_test()

    vm_name = params.get("main_vm")
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    params.update({"backup_vmxml": bkxml.copy()})
    vm = env.get_vm(vm_name)

    params.update({"original_disk_source": libvirt_disk.get_first_disk_source(vm)})
    snap_names = eval(params.get("snap_names"))
    params.update({"mem_files": ["/tmp/%s" % snap_names[0], "/tmp/%s" % snap_names[1]]})
    disk_type = params.get("disk_type")
    vm_status = params.get("vm_status")
    file_path = params.get("file_path")
    disk_dict = eval(params.get('disk_dict', '{}'))
    snapshot_dict = params.get("snapshot_dict")
    snapshot_disk_list = params.get("snapshot_disk_list")

    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    block_obj = blockcommand_base.BlockCommand(test, vm, params)
    snap_file1 = disk_obj.base_dir + "." + snap_names[0]
    snap_file2 = disk_obj.base_dir + "." + snap_names[1]

    libvirt_version.is_libvirt_feature_supported(params)

    try:
        run_test()

    finally:
        teardown_test()
