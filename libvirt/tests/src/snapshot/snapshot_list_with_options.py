#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.snapshot import snapshot_base


def run(test, params, env):
    """
    Get guest snapshot list with different options.
    """
    def create_snapshots(vm_name, snap_names):
        """
        Create snapshots for guest.

        :params vm_name: the vm name
        :params snap_names: the created snapshot names
        """
        test.log.info("TEST_SETUP: Prepare shutoff guest and create a snapshot.")
        virsh.snapshot_create_as(vm_name, "%s --disk-only" % snap_names[0], **virsh_dargs)
        test.log.info("TEST_SETUP: Prepare running guest and create a disk only snapshot.")
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        virsh.snapshot_create_as(vm_name, "%s --disk-only" % snap_names[1], **virsh_dargs)
        test.log.info("TEST_SETUP: Prepare running guest and create a disk and mempry snapshot.")
        for i in range(2, 4):
            virsh.snapshot_create_as(vm_name,
                                     "%s" % (snap_options % (snap_names[i], snap_names[i])),
                                     **virsh_dargs)
        for snap_name in snap_names:
            test_obj.check_snap_list(snap_name)

    def check_snap_list(snap_list):
        """
        Check the list snapshots of guest to match expected ones.

        :params snap_list: the snapshots created in setup
        """
        if special_check:
            list_dict = {}
            cmd = "snapshot-list %s %s" % (vm_name, list_options)
            list_result = virsh.command(cmd, **virsh_dargs).stdout_text.split()
            list_dict[list_result[0]] = ''
            for num in range(1, len(list_result), 2):
                key = list_result[num]
                value = list_result[num+1]
                list_dict[key] = value
            for key, value in expected_dict.items():
                if list_dict.get(key) != value:
                    test.fail("Get incorrect result %s, the expected result is %s" %
                              (list_result, expected_dict))
            test.log.debug("Get expected result for snapshot-list options %s",
                           list_options)
        for snap_name in expected_list:
            if snap_name not in snap_list:
                test.fail("Snapshot %s doesn't exist" % snap_name)
            else:
                test.log.info("Snapshot %s is in expected snapshot list.", snap_name)

    vm_name = params.get("main_vm")
    snap_names = eval(params.get("snap_names", "[]"))
    snap_options = params.get("snap_options")
    list_options = params.get("list_options")
    expected_list = eval(params.get("expected_list", "[]"))
    expected_dict = eval(params.get("expected_dict", "{}"))
    special_check = "yes" == params.get("special_check", "no")
    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    virsh_dargs = {"debug": True, "ignore_status": False}
    test_obj = snapshot_base.SnapshotTest(vm, test, params)

    try:
        test.log.info("STEP1: Create snapshots.")
        create_snapshots(vm_name, snap_names)
        test.log.info("STEP2: List snapshots.")
        snap_list = virsh.snapshot_list(vm_name, list_options, **virsh_dargs)
        test.log.info("STEP3: Check the listed snapshots.")
        check_snap_list(snap_list)
    finally:
        snap_names.reverse()
        test_obj.teardown_test()
        bkxml.sync()
