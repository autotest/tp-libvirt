
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
       Do blockpull when the guest image has backing file
       1) Prepare different type disk and snap chain
           File disk image which has file backing file
           File disk image which has block backing file
           Block disk image which has file backing file
           Block disk image which has block backing file
       2) Do blockpull
           with --base
           without --base
       3) Check result
           md5 of device in vm is correct
       """

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
       """
        test.log.info('Setup env: Prepare backingfile and snap chain')
        based_image, test_obj.backing_file = disk_obj.prepare_backing_file(
            disk_type, backing_file_type, backing_format)

        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict,
                                                       new_image_path=based_image)

        test_obj.backingchain_common_setup(create_snap=True,
                                           snap_num=snap_num, extra=snap_extra)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.info("After create snapshots, the vmxml is:\n%s", vmxml)

    def run_test():
        """
        Do blockpull and check backingchain result , hash value
        """
        if base_index:
            # with --base option scenario
            base_option = " --base %s" % test_obj.snap_path_list[int(base_index)-1]
        else:
            # without --base option scenario
            base_option = " "

        session = vm.wait_for_login()
        expected_hash = test_obj.get_hash_value(session,
                                                "/dev/"+test_obj.new_dev,
                                                sleep_time=20)

        test.log.info("TEST_STEP: Do blockpull")
        virsh.blockpull(vm.name, target_disk, base_option+pull_options,
                        ignore_status=False, debug=True)

        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                expected_chain)
        check_obj.check_hash_list(["/dev/"+test_obj.new_dev], [expected_hash],
                                  session, sleep_time=20)
        session.close()

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Recover test enviroment.")
        test_obj.backingchain_common_teardown()
        bkxml.sync()

        disk_obj.cleanup_disk_preparation(disk_type)
        if backing_file_type == 'block' and disk_type == 'file':
            disk_obj.cleanup_block_disk_preparation(disk_obj.vg_name,
                                                    disk_obj.lv_backing)
    # Process cartesian parameters
    vm_name = params.get("main_vm")
    disk_type = params.get('disk_type')
    disk_dict = eval(params.get('disk_dict', '{}'))
    target_disk = params.get('target_disk')
    backing_file_type = params.get('backing_file_type')
    backing_format = params.get('backing_format')
    pull_options = params.get('pull_options')
    expected_chain_index = params.get('expected_chain')
    base_index = params.get('base_image_suffix')
    snap_num = int(params.get('snap_num'))
    snap_extra = params.get('snap_extra')

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
