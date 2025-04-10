# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.snapshot import snapshot_base


def run(test, params, env):
    """
    :params test: test object
    :params params: wrapped dict with all parameters
    :params env: test object
    """
    def setup_test():
        """
        Delete snapshots in the parent external snapshots
        with multiple children.
        """
        test.log.info("TEST_SETUP: Prepare the parent external snapshot"
                      " with multiple children")
        virsh.start(vm_name)
        vm.wait_for_login().close()
        for sname in snap_names:
            virsh.snapshot_create_as(vm.name, snap_options % (sname, sname, sname),
                                     **virsh_dargs)
            if sname == 's3':
                virsh.snapshot_revert(vm_name, snap_names[0], **virsh_dargs)
        res = virsh.snapshot_current(vm_name, **virsh_dargs).stdout.strip()
        if res != snap_names[-1]:
            test.fail("Expect the current snap name is '%s' instead of"
                      " '%s'" % (snap_names[-1], res))
        virsh.snapshot_list(vm_name, **virsh_dargs, options='--tree')

    def run_test_del_parent_snap():
        """
        Delete the parent snapshot with multiple children.
        """
        test.log.info("TEST_STEP:Delete the parent snapshot with multi-child.")
        if not vm.is_alive():
            virsh.start(vm_name)
            vm.wait_for_login().close()
        del_res = virsh.snapshot_delete(vm.name, snap_names[0], debug=True)
        libvirt.check_exit_status(del_res, error_msg)

    def run_test_del_current_branch():
        """
        Delete the snapshots of current branch.
        """
        test.log.info("TEST_STEP: Delete the snapshots of current branch.")
        for times in range(1, 4):
            curr_sname = virsh.snapshot_current(vm_name, **virsh_dargs).stdout.strip()
            del_res = virsh.snapshot_delete(vm_name, curr_sname, debug=True)
            if times == 3:
                libvirt.check_exit_status(del_res, error_msg)
        for index in del_index:
            res = virsh.snapshot_delete(vm_name, snap_names[index], **virsh_dargs)
            libvirt.check_exit_status(res)

    def run_test_del_non_current_branch():
        """
        Delete the snapshots of non-current branch
        """
        test.log.info("TEST_STEP: Delete the snapshots of non-current branch.")
        res = virsh.snapshot_delete(vm_name, snap_names[1], debug=True)
        libvirt.check_exit_status(res, error_msg)

        for index in [2, 1] + del_index:
            res = virsh.snapshot_delete(vm_name, snap_names[index], **virsh_dargs)
            libvirt.check_exit_status(res)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        snap_names.reverse()
        test_obj.virsh_dargs = {'ignore_status': True, 'debug': True}
        test_obj.delete_snapshot(snap_names)
        bkxml.sync()

    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_xml.copy()
    vm = env.get_vm(vm_name)

    snap_options = params.get("snap_options")
    case = params.get("case")
    run_test = eval("run_test_%s" % case)
    error_msg = params.get("error_msg")

    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    virsh_dargs = {"debug": True, "ignore_status": False}
    snap_names = ['s1', 's2', 's3', 's4', 's5']
    del_index = eval(params.get('del_index', '[]'))
    libvirt_version.is_libvirt_feature_supported(params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
