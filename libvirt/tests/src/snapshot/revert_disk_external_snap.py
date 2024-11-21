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
    virsh.snapshot_revert(vm.name, snap_name, **virsh_dargs)

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
        vm.start()
        session = vm.wait_for_serial_login()
        mem_file = " "
        for index, sname in enumerate(snap_names):
            session.cmd("touch %s" % file_list[index])
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
    test_obj = snapshot_base.SnapshotTest(vm, test, params)
    params.update({"test_obj": test_obj})

    libvirt_version.is_libvirt_feature_supported(params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
