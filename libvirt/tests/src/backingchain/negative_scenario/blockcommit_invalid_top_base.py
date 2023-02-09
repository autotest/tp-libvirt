from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Do blockcommit for invalid top or base option.

    1) Prepare started guest and snap chain
    2) Do blockcommit:
        S1: Do blockcommit when image has no backing file
        S2: Do blockcommit using same image.
        S3: Do blockcommit from top without --active.
    3) Check error message
    """

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
       """

        test_obj.backingchain_common_setup(create_snap=True,
                                           snap_num=snap_num)

    def run_test():
        """
        Do blockcommit
        """
        test.log.info("TEST_STEP1: Get option ")
        top_option, base_option = _get_options()

        if params.get("pivot"):
            virsh.blockcommit(vm_name, target_disk, pivot,
                              ignore_status=False, debug=True)

        test.log.info("TEST_STEP2: Do blockcommit with error operation")
        result = virsh.blockcommit(vm_name, target_disk,
                                   top_option+base_option+commit_option,
                                   ignore_status=True, debug=True)

        test.log.info("TEST_STEP3: Check correct error msg after blockcommit")
        _check_result(result)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean env")
        test_obj.backingchain_common_teardown()

        bkxml.sync()

    def _get_options():
        """
        Get top, base, commit options

        :return: return the top and base option
        """
        top_option, base_option = '', ''
        if top_index.isnumeric():
            top_option = "--top %s" % test_obj.snap_path_list[int(top_index)-1]
        elif top_index == "origin_source_file":
            top_option = "--top %s" % origin_source_file

        if base_index:
            base_option = " --base %s" % test_obj.snap_path_list[int(base_index)-1]

        return top_option, base_option

    def _check_result(result):
        """
        Check blockcommit result

        :param result: The blockcommit result.
        """
        replace_msg = origin_source_file

        if case == "same_image":
            replace_msg = test_obj.snap_path_list[int(top_index) - 1]
        libvirt.check_result(result, [err_msg.format(replace_msg), err_msg_2])

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    pivot = params.get('pivot')
    snap_num = int(params.get('snap_num'))
    err_msg = params.get('err_msg')
    err_msg_2 = params.get('err_msg_2')
    top_index = params.get('top_image_suffix', '')
    base_index = params.get('base_image_suffix', '')
    commit_option = params.get('commit_option', '')
    case = params.get('case', '')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    origin_source_file = libvirt_disk.get_first_disk_source(vm)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
