# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import uuid

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.snapshot import snapshot_base


def check_genid(vm, test, old_genid, changed=False):
    """
    Check if genid is changed.

    :params vm: vm object
    :params test: test object
    :params old_genid: the old genid value to compare
    :params changed: Checking is new genid is changed, default False
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    test.log.debug("The new xml is:\n%s", vmxml)

    new_genid = vmxml.get_genid()
    if changed:
        if new_genid == old_genid:
            test.fail("Genid '%s' is same as old genid '%s'" % (new_genid, old_genid))
    else:
        if new_genid != old_genid:
            test.fail("Genid is updated from '%s' to '%s'" % (old_genid, new_genid))
    test.log.debug("Check genid successfully")


def run(test, params, env):
    """
    Revert snapshot for guest which includes genid.
    """
    def run_test():
        """
        Revert snapshot for guest which includes genid.
        """
        test.log.info("TEST_STEP1:Prepare a running guest with genid.")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        original_genid = str(uuid.uuid4())
        vmxml.setup_attrs(**{'genid': original_genid})
        vmxml.sync()
        virsh.start(vm_name)
        vm.wait_for_login().close()

        test.log.info("TEST_STEP2,3:Create disk snapshots and memory snapshots."
                      "Check snapshots and genid is not changed")
        for sname in snap_names:
            virsh.snapshot_create_as(vm.name, snap_options % (sname, sname, sname),
                                     **virsh_dargs)
            test_obj.check_snap_list(sname)
            check_genid(vm, test, original_genid)

        test.log.info("TEST_STEP4: Revert snapshot to the first snap")
        virsh.snapshot_revert(vm_name, snap_names[0], **virsh_dargs)

        test.log.info("TEST_STEP6:Check genid which has already been changed.")
        check_genid(vm, test, original_genid, changed=True)

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

    virsh_dargs = {"debug": True, "ignore_status": True}
    snap_names = eval(params.get("snap_names", '[]'))
    snap_options = params.get("snap_options")
    test_obj = snapshot_base.SnapshotTest(vm, test, params)

    try:
        libvirt_version.is_libvirt_feature_supported(params)
        run_test()

    finally:
        teardown_test()
