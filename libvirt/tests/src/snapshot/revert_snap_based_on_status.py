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
    Revert snapshots based on different domain status.
    """
    def run_test():
        """
        Revert snapshots based on different domain status.
        """
        test.log.info("TEST_STEP1: Prepare a snap.")
        if snap_status == "running":
            virsh.start(vm_name, **virsh_dargs)
            vm.wait_for_login().close()
        virsh.snapshot_create_as(vm_name, snap_options % snap_name,
                                 **virsh_dargs)
        status_result = virsh.snapshot_status(vm_name, **virsh_dargs)
        if status_result[snap_name] != snap_status:
            test.fail("%s status should be '%s' instead of '%s'" % (
                snap_name, snap_status, status_result[snap_name]))
        else:
            test.log.debug("Check snap status '%s' is correct" % snap_status)

        test.log.info("TEST_STEP2: Set domain status.")
        if vm_status.replace(' ', '') != snap_status:
            if vm_status == "shut off":
                virsh.destroy(vm_name, **virsh_dargs)
            elif vm_status == "running":
                virsh.start(vm_name, **virsh_dargs)
                vm.wait_for_login().close()

        test.log.info("TEST_STEP3: Revert the snapshot")
        virsh.snapshot_revert(vm_name, snap_name, **virsh_dargs)

        test.log.info("TEST_STEP5: Check expected domain status")
        if not libvirt.check_vm_state(vm_name, expected_status):
            test.fail("VM status should be '%s' after reverting." % expected_status)
        else:
            test.log.debug("Check vm status '%s' is correct", expected_status)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.delete_snapshot(snap_name)
        bkxml.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_xml.copy()
    virsh_dargs = {"debug": True, "ignore_status": True}
    snap_name = params.get("snap_name")
    snap_options = params.get("snap_options")
    snap_status = params.get("snap_status")
    vm_status = params.get("vm_status")
    expected_status = params.get("expected_status")
    test_obj = snapshot_base.SnapshotTest(vm, test, params)

    try:
        libvirt_version.is_libvirt_feature_supported(params)
        run_test()

    finally:
        teardown_test()
