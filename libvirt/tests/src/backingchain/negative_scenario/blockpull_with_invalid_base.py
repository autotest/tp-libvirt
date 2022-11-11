from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Do blockpull with invalid base image

    1) Prepare a running guest and snap chain.
    2) Do blockpull
        S1: Do blockpull with active image as base.
        S2: Do blockpull with nonexist base image.
    3) Check error
    """

    def setup_test():
        """
        Prepare running guest and create snap chain.
       """
        test.log.info("TEST_SETUP:Start vm and create snap chain")
        test_obj.backingchain_common_setup(create_snap=True,
                                           snap_num=snap_num)

    def run_test():
        """
        Do blockpull with invalid base image
        """
        invalid_path = ""
        if case == "active_as_base":
            invalid_path = test_obj.snap_path_list[-1]
        elif case == "not_existing_path":
            invalid_path = not_exist_file

        test.log.info("TEST_STEP:Do blockpull and check error message")
        result = virsh.blockpull(vm.name, target_disk,
                                 pull_options % invalid_path,
                                 ignore_status=True,
                                 debug=True)
        libvirt.check_result(result, expected_fails=error_msg,
                             check_both_on_error=True)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()
        bkxml.sync()

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    case = params.get('case')
    snap_num = int(params.get('snap_num'))
    not_exist_file = params.get('not_exist_file')
    pull_options = params.get('pull_options')
    error_msg = params.get('error_msg')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
