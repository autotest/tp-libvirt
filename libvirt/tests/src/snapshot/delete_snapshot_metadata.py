# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.snapshot import snapshot_base
from provider.virtual_disk import disk_base

virsh_dargs = {"debug": True, "ignore_status": False}


def check_source_and_mem_file(test, params, new_disk_source, disk_files, mem_files):
    """
    Check current vm two disks source path and mem file.

    :param: test: test object.
    :param params: Dictionary with the test parameters
    :param: new_disk_source: source path of new disk.
    :param: disk_files: disk source files.
    :param: mem_file: memory snapshot files.
    """
    new_disk = params.get("new_disk")
    original_disk = params.get("original_disk")
    disk_type = params.get("disk_type")
    original_disk_source = params.get("original_disk_source")

    vmxml = vm_xml.VMXML.new_from_dumpxml(params.get("main_vm"))
    test.log.debug("Get current xml is :%s", vmxml)

    disk1_sources = disk_base.DiskBase.get_source_list(vmxml, disk_type, original_disk)[0]
    disk2_sources = disk_base.DiskBase.get_source_list(vmxml, disk_type, new_disk)[::-1]
    if disk1_sources != original_disk_source:
        test.fail("Expect to get '%s' in system disk instead of '%s'" % (
            original_disk_source, disk1_sources))
    test.log.debug('Check system disk source list success')
    if disk2_sources != [new_disk_source] + disk_files:
        test.fail("Expect to get '%s' in new disk but get '%s'" % (
            [new_disk_source] + disk_files, disk2_sources))
    test.log.debug('Check the new disk source list success')

    for mf in mem_files:
        if not os.path.exists(mf):
            test.fail("Mem file '%s' does not exist" % mf)


def run(test, params, env):
    """
    Test deleting snapshot's metadata.
    """

    def run_test():
        """
        Test deleting snapshot's metadata.
        """
        del_option = params.get('del_option')
        snap_name = eval(params.get("snap_name"))
        snap_options = params.get("snap_options")
        mem_path = params.get("mem_path")
        disk_path = params.get("disk_path")
        disk_dict = eval(params.get("disk_dict"))
        disk_type = params.get("disk_type")

        test.log.info("TEST_STEP1: Prepare a new disk vdb")
        new_disk_source = disk_obj.add_vm_disk(disk_type, disk_dict)
        virsh.start(vm_name, **virsh_dargs)
        vm.wait_for_login().close()

        test.log.info("TEST_STEP2:Create 3 external disk+memory snapshots,"
                      "check the new disk source list and memory snapshot file.")
        mem_files, disk_files = [], []
        for index, sname in enumerate(snap_name):
            virsh.snapshot_create_as(vm_name, snap_options % (sname, index + 1,
                                                              index + 1),
                                     **virsh_dargs)
            mem_files.append(mem_path + str(index + 1))
            disk_files.append(disk_path + str(index + 1))
        check_source_and_mem_file(test, params, new_disk_source, disk_files, mem_files)

        test.log.info("TEST_STEP3: Delete the first snapshot with --metadata"
                      "check the new disk source list and memory snapshot file.")
        virsh.snapshot_delete(vm_name, snap_name[0], options=del_option, **virsh_dargs)
        check_source_and_mem_file(test, params, new_disk_source, disk_files, mem_files)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.teardown_test()

    libvirt_version.is_libvirt_feature_supported(params)
    try:
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)
        original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        params['backup_vmxml'] = original_xml.copy()
        params.update({"original_disk_source": libvirt_disk.get_first_disk_source(vm)})
        test_obj = snapshot_base.SnapshotTest(vm, test, params)
        disk_obj = disk_base.DiskBase(test, vm, params)

        run_test()

    finally:
        teardown_test()
