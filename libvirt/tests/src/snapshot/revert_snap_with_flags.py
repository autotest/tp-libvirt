# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.snapshot import snapshot_base


def check_vm_state_in_dom_list(test, vm_name, vm_state):
    """
    Check vm state in virsh domain list.

    :param test: test object.
    :param vm_name: vm name.
    :param vm_state: expected vm state.
    """
    doms = virsh.dom_list("--all", debug=True).stdout.strip()
    if not re.search(r".*%s.*%s.*" % (vm_name, vm_state), doms):
        test.fail(
            "VM '%s' with the state %s is not found" % (vm_name, vm_state))


def run(test, params, env):
    """
    Revert snapshot for guest with different flags.
    """
    def run_test():
        """
        Revert snapshot with --current --paused --running
        --reset-nvram --force in order.
        """
        test.log.info("TEST_STEP1:Prepare shutoff guest and create a snapshot.")
        virsh.snapshot_create_as(vm_name,
                                 snap1_options % (snap_names[0], snap_names[0]),
                                 **virsh_dargs)
        test_obj.check_snap_list(snap_names[0])

        test.log.info("TEST_STEP2:Start guest and create a snapshot.")
        vm.start()
        vm.wait_for_login().close()
        virsh.snapshot_create_as(vm_name, snap2_options % (
            snap_names[1], snap_names[1], snap_names[1]), **virsh_dargs)
        test_obj.check_snap_list(snap_names[1])

        test.log.info("TEST_STEP3: Revert to the second snap.")
        virsh.snapshot_revert(vm_name, revert_flags[0], **virsh_dargs)
        check_vm_state_in_dom_list(test, vm_name, vm_state="paused")

        test.log.info("TEST_STEP4: Revert snapshot to the first snap")
        vars_hash1 = process.run("sha256sum %s" % vars_path, ignore_status=False)
        virsh.snapshot_revert(vm_name, snap_names[0],
                              options=revert_flags[1], **virsh_dargs)
        vars_hash2 = process.run("sha256sum %s" % vars_path, ignore_status=False)
        if vars_hash1 == vars_hash2:
            test.fail("Expect to get different hash values for '%s', "
                      "but got both '%s' " % (vars_path, vars_hash1))

        test.log.info("TEST_STEP5: Revert snapshot to the second snap force")
        virsh.snapshot_revert(vm_name, snap_names[1],
                              options=revert_flags[2], **virsh_dargs)
        check_vm_state_in_dom_list(test, vm_name, vm_state="running")

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        snap_names.reverse()
        test_obj.teardown_test()

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    params['backup_vmxml'] = original_xml.copy()
    vm = env.get_vm(vm_name)
    virsh_dargs = {"debug": True, "ignore_status": False}
    snap_names = eval(params.get("snap_names", '[]'))
    revert_flags = eval(params.get("flags", "[]"))
    vars_path = params.get("vars_path")
    snap1_options, snap2_options = params.get("snap1_options"), params.get('snap2_options')
    test_obj = snapshot_base.SnapshotTest(vm, test, params)

    try:
        run_test()

    finally:
        teardown_test()
