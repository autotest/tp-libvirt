# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os
import re

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.snapshot import snapshot_base
from provider.virtual_disk import disk_base

virsh_dargs = {"debug": True, "ignore_status": False}


def check_guest_domblklist(test, params, vm_name):
    """
    Check current guest domblklist.

    :params: test, test object.
    :params: params, cfg parameter dict.
    :params: vm_name, guest name.
    """
    disk_type = params.get('disk_type')
    disk1, disk2 = params.get("disk1"), params.get("disk2")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    test.log.debug("Current vm xml is %s\n", vmxml)
    disk1_sources = disk_base.DiskBase.get_source_list(vmxml, disk_type, disk1)[::-1]
    disk2_sources = disk_base.DiskBase.get_source_list(vmxml, disk_type, disk2)[::-1]
    if disk2_sources:
        pattern = [r'%s\s*%s' % (disk1, disk1_sources[-1]),
                   r'%s\s*%s' % (disk2, disk2_sources[-1])]
    else:
        pattern = [r'%s\s*%s' % (disk1, disk1_sources[-1])]

    domblk_res = virsh.domblklist(vm_name, **virsh_dargs).stdout_text.strip()
    for pat in pattern:
        if not re.search(pat, domblk_res):
            test.fail('Expected to get "%s" in "%s"' % (pat, domblk_res))


def run(test, params, env):
    """
    Verify deleting snapshot after disk attached.
    """
    def run_test():
        """
        Verify deleting snapshot after disk attached.
        """
        vm_name = params.get("main_vm")
        original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        params['backup_vmxml'] = original_xml.copy()
        vm = env.get_vm(vm_name)
        disk_type = params.get('disk_type')
        snap_names = eval(params.get("snap_names", '[]'))
        mem_path = params.get("mem_path")
        disk1_snap_option = params.get("disk1_snap_option")
        disk2_snap_option = params.get("disk2_snap_option")
        disk_type, disk_dict = params.get("disk_type"), eval(params.get("disk_dict", '{}'))
        disk1, disk2 = params.get("disk1"), params.get("disk2")
        snap_deleting_ongoing_tag = params.get("snap_deleting_ongoing_tag")

        test.log.info("TEST_STEP1:Start guest with just system disk.")
        vm.wait_for_login().close()
        check_guest_domblklist(test, params, vm_name)

        test.log.info("TEST_STEP2: Create two disk only snaps for guest")
        for sname in snap_names[0:2]:
            virsh.snapshot_create_as(vm.name, disk1_snap_option % sname,
                                     **virsh_dargs)

        test.log.info("TEST_STEP3: Attach a virtual disk ")
        new_disk, disk2_path = disk_obj.prepare_disk_obj(disk_type, disk_dict)
        virsh.attach_device(vm_name, new_disk.xml, **virsh_dargs)
        check_guest_domblklist(test, params, vm_name)

        test.log.info("TEST_STEP4: Create disk and mem snapshot for new disk")
        virsh.snapshot_create_as(vm_name, disk2_snap_option % (
            snap_names[2], snap_names[2]), **virsh_dargs)
        check_guest_domblklist(test, params, vm_name)
        if not os.path.exists(mem_path + snap_names[2]):
            test.fail("Mem file '%s' does not exist" % mem_path + snap_names[2])

        test.log.info("TEST_STEP5: Create disk only snapshot for guest.")
        virsh.snapshot_create_as(vm.name, disk1_snap_option % snap_names[3],
                                 **virsh_dargs)
        check_guest_domblklist(test, params, vm_name)

        test.log.info("TEST_STEP6: Delete the third snap and check it "
                      "actually removed")
        virsh.snapshot_delete(vm_name, snap_names[2], **virsh_dargs)
        test_obj.check_snap_list(snap_names[2], expect_exist=False)

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        disk2_sources = disk_obj.get_source_list(vmxml, disk_type, disk2)
        snap3_path = disk2_path + ".%s" % snap_names[2]
        if snap3_path in disk2_sources:
            test.fail("Expect '%s' does not exist in new disk source list '%s'" % (
                snap3_path, disk2_sources))

        expect_path = os.path.join(mem_path, snap_names[2])
        if os.path.exists(expect_path):
            test.fail("Expect the mem file '%s' does not exist, but still found" % expect_path)

        snap_xml = virsh.snapshot_dumpxml(
            vm_name, snap_names[1], **virsh_dargs).stdout_text.strip("\n")
        if re.search(snap_deleting_ongoing_tag, snap_xml):
            test.fail("Expect no '%s' in guest xml, but still found" % snap_deleting_ongoing_tag)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.teardown_test()
        disk_type = params.get('disk_type')
        disk_obj.cleanup_disk_preparation(disk_type)

    vm = env.get_vm(params.get("main_vm"))
    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    libvirt_version.is_libvirt_feature_supported(params)

    try:
        run_test()

    finally:
        teardown_test()
