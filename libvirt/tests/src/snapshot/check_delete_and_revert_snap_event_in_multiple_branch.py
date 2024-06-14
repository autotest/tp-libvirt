# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.snapshot import snapshot_base

virsh_dargs = {"debug": True, "ignore_status": False}


def check_event_output(test, pattern, event_session, expect_exist=True):
    """
    Check event output.

    :param test: test object
    :param pattern: the check event pattern, list type
    :param event_session: event session to get event output
    :param expect_exist: expect the pattern exist or not
    """
    output = virsh.EventTracker.finish_get_event(event_session)
    event_session.close()
    for check_patt in pattern:
        is_existed = bool(re.findall(check_patt, output))
        if is_existed != expect_exist:
            test.fail('Expect %s to get: %s from the event output:%s' % (
                '' if expect_exist else 'not', check_patt, output))
        else:
            test.log.debug("Check event %s success in the event output", check_patt)


def delete_snap_and_check_event(test, params, del_snap, expected_event,
                                expect_exist=True):
    """
    Delete snap and check expected event

    :param test: test object.
    :param params: wrapped dict with all parameters
    :param del_snap: the snapshot to be deleted
    :param expected_event: expected event value
    :param expect_exist: expected event exist or not.
    """
    vm_name = params.get("main_vm")

    event_session = virsh.EventTracker.start_get_event(vm_name)
    virsh.snapshot_delete(vm_name, del_snap, **virsh_dargs)
    check_event_output(test, expected_event, event_session, expect_exist)


def run(test, params, env):
    """
    :params test: test object
    :params params: wrapped dict with all parameters
    :params env: test object
    """
    def setup_test():
        """
        Prepare the parent external snapshot with multiple children.
        snapshot tree is like this
        s1
         |
         +- s2
         |   |
         |   +- s3
         |
         +- s4
             |
             +- s5
        """
        test.log.info("TEST_SETUP: Prepare the parent external snapshot "
                      "with multiple children:")
        virsh.start(vm_name)
        vm.wait_for_login().close()
        for sname in snap_names:
            virsh.snapshot_create_as(vm.name, snap_options % (sname, sname, sname),
                                     **virsh_dargs)
            if sname == 's3':
                virsh.snapshot_revert(vm_name, snap_names[0], **virsh_dargs)

        virsh.snapshot_list(vm_name, **virsh_dargs, options='--tree')
        res = virsh.snapshot_current(vm_name, **virsh_dargs).stdout.strip()
        if res != snap_names[-1]:
            test.fail("Expect the current snap name is '%s' instead of"
                      " '%s'" % (snap_names[-1], res))

    def run_test():
        """
        Delete the snapshots of current branch.
        """
        test.log.info("TEST_STEP1,2,3: Delete the snap of active chain "
                      "and check event.")
        delete_snap_and_check_event(test, params, snap_names[-1], del_event)

        test.log.info("TEST_STEP4: Revert the snap of inactive chain "
                      "and check event.")
        event_session = virsh.EventTracker.start_get_event(vm_name)
        virsh.snapshot_revert(vm_name, snap_names[1], **virsh_dargs)
        check_event_output(test, revert_event, event_session)

        test.log.info("TEST_STEP5: Delete the third snap and check event.")
        delete_snap_and_check_event(test, params, snap_names[2],
                                    del_event, expect_exist=False)

        test.log.info("TEST_STEP6: Delete the third snap and check event.")
        delete_snap_and_check_event(test, params, snap_names[3],
                                    del_event, expect_exist=False)

        test.log.info("TEST_STEP7: Delete the first snap and check event.")
        delete_snap_and_check_event(test, params, snap_names[0],
                                    del_event)

        test.log.info("TEST_STEP8: Delete the first snap and check event.")
        delete_snap_and_check_event(test, params, snap_names[1],
                                    [del_event[1]] + extra_del_devent)

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
    bkxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vm = env.get_vm(vm_name)

    snap_options = params.get("snap_options")
    snap_names = eval(params.get("snap_names"))
    del_event = eval(params.get("del_event"))
    disk_source = libvirt_disk.get_first_disk_source(vm)
    extra_del_devent = eval(params.get("extra_del_devent") % disk_source)
    revert_event = eval(params.get("revert_event1"))+eval(params.get("revert_event2"))

    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    libvirt_version.is_libvirt_feature_supported(params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
