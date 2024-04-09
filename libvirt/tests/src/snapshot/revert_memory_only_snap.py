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

from provider.snapshot import snapshot_base
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Revert guest to memory only snapshot.
    """
    def run_test():
        """
        Revert guest to memory only snapshot.
        """
        test.log.info("TEST_STEP1:Create memory only snaps for running guest.")
        virsh.start(vm_name)
        vm.wait_for_login().close()
        for sname in snap_names:
            virsh.snapshot_create_as(vm.name, snap_options % (sname, sname),
                                     **virsh_dargs)
            test_obj.check_snap_list(sname)
        test.log.info("TEST_STEP2:Revert the second snap and check source path")
        virsh.snapshot_revert(vm_name, snap_names[1], **virsh_dargs)

        def _check_source_num():
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            source_list = disk_obj.get_source_list(vmxml, disk_type, target_disk)
            if len(source_list) != 2:
                test.fail('Expected disk source num should be 2 instead of %s '
                          'in xml %s' % (len(source_list), vmxml))
        _check_source_num()

        test.log.info("TEST_STEP3:Revert the first snap and check source path")
        virsh.snapshot_revert(vm_name, snap_names[0], **virsh_dargs)
        _check_source_num()

        test.log.info("TEST_STEP4:Revert the last snap and check source path")
        virsh.snapshot_revert(vm_name, snap_names[2], **virsh_dargs)
        _check_source_num()

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
    disk_type = params.get("disk_type")
    target_disk = params.get("target_disk")
    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    libvirt_version.is_libvirt_feature_supported(params)

    try:
        run_test()

    finally:
        teardown_test()
