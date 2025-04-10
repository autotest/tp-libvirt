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
from virttest.utils_libvirt import libvirt_misc

from provider.snapshot import snapshot_base


def run(test, params, env):
    """
    Revert snapshots based on different domain state.
    """

    def get_snap_state(vm_name):
        """
        Get list of snapshots state of domain.

        :param vm_name: name of domain
        :return: dict of snapshot names and snapshot state,
         e.g.: get {'s1': 'running', 's2': 'running'}
        """
        result = virsh.command("snapshot-list %s" % vm_name,
                               **virsh_dargs).stdout_text.strip()
        state_dict = libvirt_misc.convert_to_dict(
            result, r"(\S*) *\d+.* \d*:\d*:\d* .\d* *(\S*)")
        test.log.debug("Get snap name and state dict is :%s", state_dict)
        return state_dict

    def run_test():
        """
        Revert snapshots based on different domain state.
        """
        test.log.info("TEST_STEP1: Prepare a snap.")
        if snap_state == "running":
            virsh.start(vm_name, **virsh_dargs)
            vm.wait_for_login().close()
        virsh.snapshot_create_as(vm_name, snap_options % snap_name,
                                 **virsh_dargs)
        state_result = get_snap_state(vm_name)
        if state_result[snap_name] != snap_state:
            test.fail("%s state should be '%s' instead of '%s'" % (
                snap_name, snap_state, state_result[snap_name]))
        else:
            test.log.debug("Check snap state '%s' is correct" % snap_state)

        test.log.info("TEST_STEP2: Set domain state.")
        if vm_state.replace(' ', '') != snap_state:
            if vm_state == "shut off":
                virsh.destroy(vm_name, **virsh_dargs)
            elif vm_state == "running":
                virsh.start(vm_name, **virsh_dargs)
                vm.wait_for_login().close()

        test.log.info("TEST_STEP3: Revert the snapshot")
        virsh.snapshot_revert(vm_name, snap_name, **virsh_dargs)

        test.log.info("TEST_STEP5: Check expected domain state")
        if not libvirt.check_vm_state(vm_name, expected_state):
            test.fail("VM state should be '%s' after reverting." % expected_state)
        else:
            test.log.debug("Check vm state '%s' is correct", expected_state)

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
    snap_state = params.get("snap_state")
    vm_state = params.get("vm_state")
    expected_state = params.get("expected_state")
    test_obj = snapshot_base.SnapshotTest(vm, test, params)

    libvirt_version.is_libvirt_feature_supported(params)
    try:
        run_test()
    finally:
        teardown_test()
