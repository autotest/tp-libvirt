from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Do blockcommit with --keep-overlay option
    1) Prepare disk and snap chain
        disk types: file
    2) Do blockcommit:
        Do active blockcommit.
        Do inactive blockcommit.
    3) Check result:
        vmxml chain
        hash value of file in vm are correct
    """

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
       """
        test.log.info("TEST_SETUP: Prepare disk and snap")
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test_obj.prepare_snapshot(snap_num=snap_num)
        check_obj.check_backingchain(test_obj.snap_path_list[::-1])

    def run_test():
        """
        Do blockcommit and check backingchain result , disk hash value
        """
        test.log.info("TEST_STEP1: Get top or base option and hash value.")
        session = vm.wait_for_login()
        expected_hash = test_obj.get_hash_value(session,
                                                check_item="/dev/"+test_obj.new_dev,
                                                sleep_time=20)
        top_option, base_option = _get_options()

        test.log.info("TEST_STEP2: Do bloccommit.")
        res = virsh.blockcommit(vm.name, target_disk,
                                commit_options+top_option+base_option, debug=True)
        _check_blockcommit_result(res)
        check_obj.check_hash_list(["/dev/"+test_obj.new_dev], [expected_hash],
                                  session, sleep_time=20)

        session.close()

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("Clean the env")
        test_obj.backingchain_common_teardown()

        bkxml.sync()
        disk_obj.cleanup_disk_preparation(disk_type)

    def _check_blockcommit_result(res):
        """
        Check blockcommit result according to active and inactive scenarios
        """
        if test_scenario == "active":
            libvirt.check_exit_status(res)
            expected_chain = test_obj.convert_expected_chain(expected_chain_index)
            check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                    expected_chain)
        elif test_scenario == "inactive":
            libvirt.check_exit_status(res, expect_error=True)
            libvirt.check_result(res, err_msg)

    def _get_options():
        """
        Set options
        """
        top_option, base_option = '', ''
        if top_index:
            top_option = " --top %s" % test_obj.snap_path_list[
                int(top_index) - 1]
        if base_index:
            base_option = " --base %s" % test_obj.snap_path_list[
                int(base_index) - 1]

        return top_option, base_option

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get('disk_type')
    disk_dict = eval(params.get('disk_dict', '{}'))
    snap_num = int(params.get('snap_num'))
    commit_options = params.get('commit_options')
    expected_chain_index = params.get('expected_chain')
    top_index = params.get('top_image_suffix')
    base_index = params.get('base_image_suffix')
    test_scenario = params.get('test_scenario')
    err_msg = params.get('err_msg')

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
