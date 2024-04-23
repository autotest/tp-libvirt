# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from virttest import libvirt_version
from virttest import utils_disk
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.snapshot import snapshot_base
from provider.virtual_disk import disk_base


def create_data_in_guest(params, session, partition=False):
    """
    Create data in guest.

    :param params: dict, test parameters
    :param session: vm session
    :param partition: the flag to confirm if need do partition.
    """
    disk2 = params.get("disk2")
    file_name = params.get("file_name")

    if partition:
        cmd = "mkfs.ext4 /dev/%s;mount /dev/%s /mnt" % (disk2, disk2)
        session.cmd_status_output(cmd)
    utils_disk.dd_data_to_vm_disk(session, file_name)


def check_xml_and_hash(params, test, session, disk_path, hash_value,
                       del_second_snap=False):
    """
    Check guest xml and data file expected hash value

    :param params: dict, test parameters
    :param test: test object
    :param session: guest session
    :param disk_path: disk path
    :param hash_value: disk path
    :param del_second_snap: the flag to confirm if it's after the step of deleting
    the 2nd snap.
    """
    disk_type = params.get("disk_type")
    file_name = params.get("file_name")
    disk1, disk2 = params.get("disk1"), params.get("disk2")

    new_xml = vm_xml.VMXML.new_from_dumpxml(params.get("main_vm"))
    disk2_source = disk_base.DiskBase.get_source_list(new_xml, disk_type, disk2)[::-1]
    disk1_source = disk_base.DiskBase.get_source_list(new_xml, disk_type, disk1)

    # Check new disk source.
    if del_second_snap:
        if disk2_source != [disk_path]:
            test.fail("Expect to get %s in new disk, but got '%s'" % (
                [disk_path], disk2_source))
    else:
        if disk2_source != [disk_path, params.get("snap_file2")]:
            test.fail("Expect to get %s in new disk, but got '%s'" % (
                [disk_path, params.get("snap_file2")], disk2_source))

    # Check system disk source.
    if params.get("first_disk_source") != disk1_source[0]:
        test.fail("Expect to get %s in system disk, but got '%s'" % (
            params.get("first_disk_source"), disk1_source[0]))

    # Check file hash value.
    file_hash_new, _ = params.get("block_obj").get_hash_value(
        session, check_item=file_name)
    if hash_value != file_hash_new:
        test.fail("Expect to get the same file hash value but got"
                  " '%s' and '%s'" % (hash_value, file_hash_new))
    test.log.debug("Check guest two disk xml and guest file hash successfully")


def run(test, params, env):
    """
    Delete guest to disk only snapshot.
    """
    def run_test():
        """
        Delete guest to disk only snapshot.
        """
        test.log.info("TEST_STEP1:Prepare a running vm with new non-OS disk.")
        new_path = disk_obj.add_vm_disk(disk_type, disk_dict)
        virsh.start(vm_name)
        vm_session = vm.wait_for_login()

        test.log.info("TEST_STEP2:Mount new disk to and generate random data.")
        create_data_in_guest(params, vm_session, partition=True)

        test.log.info("TEST_STEP3:Create the 1st snapshot with snapshot xml.")
        snap_file1 = disk_obj.base_dir+"."+snap_names[0]
        test_obj.create_snapshot_by_xml(
            eval(snapshot_dict % snap_names[0]),
            eval(snapshot_disk_list % snap_file1), options=snap_options)

        test.log.info("TEST_STEP4:Generate random data to new disk")
        create_data_in_guest(params, vm_session)

        test.log.info("TEST_STEP5:Create the 2nd snapshot with snapshot xml.")
        params.update({"snap_file2": disk_obj.base_dir+"."+snap_names[1]})
        test_obj.create_snapshot_by_xml(
            eval(snapshot_dict % snap_names[1]),
            eval(snapshot_disk_list % params.get("snap_file2")), options=snap_options)

        test.log.info("TEST_STEP6:Generate random data to new disk for the same"
                      "file and And get the file's sha256sum value")
        create_data_in_guest(params, vm_session)
        file_hash, _ = block_obj.get_hash_value(vm_session, check_item=file_name)

        test.log.info("TEST_STEP7:Delete the inactive snapshot (1st snapshot)"
                      "and check guest xml, hash value")
        virsh.snapshot_delete(vm_name, snap_names[0], **virsh_dargs)
        check_xml_and_hash(params, test, vm_session, new_path, file_hash)

        test.log.info("TEST_STEP8:Delete the active snapshot (2nd snapshot) "
                      "and check guest xml, hash value")
        virsh.snapshot_delete(vm_name, snap_names[1], **virsh_dargs)
        check_xml_and_hash(params, test, vm_session, new_path, file_hash,
                           del_second_snap=True)
        vm_session.close()

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        snap_names.reverse()
        test_obj.teardown_test()

    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    params['backup_vmxml'] = original_xml.copy()
    vm = env.get_vm(vm_name)

    virsh_dargs = {"debug": True, "ignore_status": False}
    disk_dict = eval(params.get("disk_dict", "{}"))
    snap_names = eval(params.get("snap_names", '[]'))
    snap_options = params.get("snap_options")
    file_name = params.get("file_name")
    disk_type = params.get("disk_type")
    snapshot_dict = params.get("snapshot_dict")
    snapshot_disk_list = params.get("snapshot_disk_list")
    params.update({"first_disk_source": libvirt_disk.get_first_disk_source(vm)})

    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    block_obj = blockcommand_base.BlockCommand(test, vm, params)
    params.update({"block_obj": block_obj})

    libvirt_version.is_libvirt_feature_supported(params)

    try:
        run_test()

    finally:
        teardown_test()
