from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Do blockcommit with --shallow option

    1) Prepare different type disk and snap chain
        disk types: file ,block
    2) Do blockcommit:
        from top to middle
    3) Check result:
        hash value of file in vm are correct
    """

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
       """
        test.log.info("Setup env: Add disk and snap chain.")
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        if case == "reuse_external":
            _, test_obj.snap_path_list = libvirt_disk.\
                create_reuse_external_snapshots(vm, disk_target=target_disk,
                                                snapshot_chain_lenghth=snap_num,
                                                create_source_file=True,
                                                snap_extra=snap_extra)
        elif case == "disk_only":
            test_obj.prepare_snapshot(snap_num=snap_num)

    def run_test():
        """
        Do blockcommit and check backingchain result, file hash value
        """
        test.log.info("TEST_STEP1: Get hash value for %s", test_obj.new_dev)
        session = vm.wait_for_login()
        expected_hash = test_obj.get_hash_value(session, "/dev/"+test_obj.new_dev)

        test.log.info("TEST_STEP2: Do blockcommit with %s", commit_options)
        virsh.blockcommit(vm.name, target_disk, commit_options,
                          ignore_status=False, debug=True)

        test.log.info("TEST_STEP3: Abort job with %s", abort_option)
        virsh.blockjob(vm_name, target_disk, abort_option, ignore_status=True,
                       debug=False)

        test.log.info("TEST_STEP4: Compare the backing list and hash values ")
        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                expected_chain)
        check_obj.check_hash_list(["/dev/"+test_obj.new_dev], [expected_hash], session)
        session.close()

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()

        bkxml.sync()
        disk_obj.cleanup_disk_preparation(disk_type)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get('disk_type')
    disk_dict = eval(params.get('disk_dict', '{}'))
    snap_extra = params.get('snap_extra', '')
    commit_options = params.get('commit_options')
    abort_option = params.get('abort_option')
    expected_chain_index = params.get('expected_chain')
    case = params.get('case')
    snap_num = int(params.get('snap_num'))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
