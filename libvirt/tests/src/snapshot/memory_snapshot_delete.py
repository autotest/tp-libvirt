#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng <dzheng@redhat.com>
#

"""
Test cases about memory snapshot deletion
"""
import os

from virttest import data_dir
from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.snapshot import snapshot_base
from provider.virtual_disk import disk_base


def create_snapshots(test_obj):
    """
    Create snapshots for the test

    :param test_obj: SnapshotTest object
    """
    for count in [1, 2]:
        snap_name = 'snap%d' % count
        mem_snap_file = os.path.join(data_dir.get_tmp_dir(), 'memory_%s.dump' % snap_name)
        snap_dict = eval(test_obj.params.get('snapshot_dict') % (snap_name, mem_snap_file))
        snap_disk_list = eval(test_obj.params.get('snapshot_disk_list'))
        test_obj.test.log.debug("Step: Create snapshot '%s'" % snap_name)
        test_obj.create_snapshot_by_xml(snap_dict, snap_disk_list)
        test_obj.check_snap_list(snap_name, options='', expect_exist=True)
        if count == 1 and test_obj.params.get('vm_status') == 'paused':
            virsh.resume(test_obj.vm.name, **test_obj.virsh_dargs)
            virsh.suspend(test_obj.vm.name, **test_obj.virsh_dargs)


def prepare_vm_state(test_obj):
    """
    Make the vm in expected state

    :param test_obj: SnapshotTest object
    """
    vm_status = test_obj.params.get('vm_status')
    virsh.start(test_obj.vm.name, **test_obj.virsh_dargs)
    test_obj.vm.wait_for_login().close()
    if vm_status == 'paused':
        virsh.suspend(test_obj.vm.name, **test_obj.virsh_dargs)


def add_new_disk(test_obj):
    """
    Add the second disk to the vm for the test

    :param test_obj: SnapshotTest object
    """
    disk_dict = eval(test_obj.params.get('disk_dict', '{}'))
    disk_base_obj = disk_base.DiskBase(test_obj.test, test_obj.vm, test_obj.params)
    disk_base_obj.new_image_path = disk_base_obj.add_vm_disk('file', disk_dict)
    return disk_base_obj


def setup_test(test_obj):
    """
    Create snapshots for the test

    :param test_obj: SnapshotTest object
    """
    disk_base_obj = add_new_disk(test_obj)
    test_obj.params['disk_base_obj'] = disk_base_obj
    prepare_vm_state(test_obj)
    test_obj.test.log.debug("Now vm xml after adding the disk:\n"
                            "%s", vm_xml.VMXML.new_from_inactive_dumpxml(test_obj.vm.name))
    create_snapshots(test_obj)


def run_test(test_obj):
    """
    Create snapshots for the test

    :param test_obj: SnapshotTest object
    """
    for count in [1, 2]:
        snap_name = 'snap%d' % count
        test_obj.test.log.debug("Step: Delete snapshot '%s'" % snap_name)
        options = '' if count == 1 else '--current'
        snap_name_delete = '' if options == '--current' else snap_name
        test_obj.delete_snapshot(snap_name_delete, options=options)
        test_obj.check_snap_list(snap_name, expect_exist=False)
        test_obj.test.log.debug("Checkpoint: The snapshot '%s' is deleted", snap_name)
        mem_snap_file = os.path.join(data_dir.get_tmp_dir(), 'memory_%s.dump' % snap_name)
        if os.path.exists(mem_snap_file):
            test_obj.test.fail("The memory snapshot file '%s' "
                               "should not exist any more after "
                               "the snapshot is deleted" % mem_snap_file)
        else:
            test_obj.test.log.debug("Checkpoint: The memory snapshot file '%s' "
                                    "does not exist any more as expected "
                                    "after the snapshot is deleted", mem_snap_file)


def teardown_test(test_obj):
    """
    Teardown for the test

    :param test_obj: SnapshotTest object
    """
    disk_base_obj = test_obj.params['disk_base_obj']
    disk_base_obj.cleanup_disk_preparation('file')
    test_obj.teardown_test()


def run(test, params, env):
    """
    Deletion test for memory only snapshot
    """
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    params['backup_vmxml'] = bkxml
    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    try:
        setup_test(test_obj)
        run_test(test_obj)
    finally:
        teardown_test(test_obj)
