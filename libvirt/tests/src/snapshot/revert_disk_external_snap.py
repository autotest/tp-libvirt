# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os

from virttest import data_dir
from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml

from provider.snapshot import snapshot_base
from provider.virtual_disk import disk_base

virsh_dargs = {"debug": True, "ignore_status": False}


def revert_snap_and_check_files(params, vm, test, snap_name, expected_files):
    """
    Revert the snapshot name and check expected file.

    :param params: dict wrapped with params.
    :param vm: vm object.
    :param test: test object.
    :param snap_name: snapshot name.
    :param expected_files: expected file list.
    """
    snap_type = params.get("snap_type")
    options = ""

    if snap_type == "disk_only" and libvirt_version.version_compare(10, 10, 0):
        options = " --running"

    virsh.snapshot_revert(vm.name, snap_name, options=options, **virsh_dargs)

    vm.cleanup_serial_console()
    vm.create_serial_console()
    session = vm.wait_for_serial_login()
    for file in expected_files:
        output = session.cmd('ls %s' % file)
        if file not in output:
            test.fail("File %s not exist in %s" % (file, output))
    params.get("test_obj").check_current_snapshot(snap_name)

    test.log.debug("File %s exist in guest" % expected_files)
    session.close()


def run(test, params, env):
    """
    Revert guest to a disk external snapshot.
    """
    def setup_test():
        """
        Prepare file and snapshot.
        """
        test.log.info("TEST_SETUP: Create files and snaps in running guest.")
        if with_data_file:
            data_file = data_dir.get_data_dir() + "/datastore.img"
            data_file_option = params.get("data_file_option", "") % data_file
            image_path = disk_obj.add_vm_disk(disk_type, disk_dict, new_image_path="",
                                              extra=data_file_option)
            new_file.extend([data_file, image_path])
        vm.start()
        test.log.debug("The current guest xml is: %s", virsh.dumpxml(vm_name).stdout_text)
        session = vm.wait_for_serial_login()
        mem_file = " "
        for index, sname in enumerate(snap_names):
            session.cmd("touch %s" % file_list[index])
            session.cmd("sync")
            if snap_type == "disk_and_memory":
                mem_file = sname
            virsh.snapshot_create_as(vm.name, snap_options % (sname, mem_file),
                                     **virsh_dargs)
            test_obj.check_snap_list(sname)

        session.close()

    def run_test():
        """
        Revert guest and check file list.
        """
        test.log.info("TEST_STEP1:Revert the 2nd snapshot and check files.")
        revert_snap_and_check_files(params, vm, test, snap_names[1],
                                    file_list[:2])

        test.log.info("TEST_STEP2:Revert the first snapshot and check files.")
        revert_snap_and_check_files(params, vm, test, snap_names[0],
                                    [file_list[0]])

        test.log.info("TEST_STEP3:Revert the last snap and check source files")
        revert_snap_and_check_files(params, vm, test, snap_names[-1],
                                    file_list)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        for file in new_file:
            if os.path.exists(file):
                os.remove(file)
        snap_names.reverse()
        test_obj.teardown_test()

    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    params['backup_vmxml'] = original_xml.copy()
    vm = env.get_vm(vm_name)

    snap_names = eval(params.get("snap_names", '[]'))
    snap_type = params.get("snap_type")
    snap_options = params.get("snap_options")
    file_list = eval(params.get("file_list"))
    disk_target = params.get("disk_target", "vdb")
    disk_type = params.get("disk_type", "file")
    disk_dict = eval(params.get("disk_dict", "{}"))
    with_data_file = "yes" == params.get("with_data_file", "no")
    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    params.update({"test_obj": test_obj})

    libvirt_version.is_libvirt_feature_supported(params)
    new_file = []

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
