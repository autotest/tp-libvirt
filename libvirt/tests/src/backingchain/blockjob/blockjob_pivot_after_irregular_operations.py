import os

from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions


def run(test, params, env):
    """
    Do blockjob --pivot after irregular operations

    S1: Do blockjob --pivot before block copy job is finished
    S2: Do blockjob --pivot after deleting the copy file
    """

    def setup_test():
        """
        Prepare active guest
        """
        test.log.info("TEST_SETUP:Setup env")
        test_obj.backingchain_common_setup()

    def run_before_finish():
        """
        Do blockjob --pivot before block copy job is finished.
        """
        cmd = blockcopy_options % (vm_name, target_disk, tmp_copy_path)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)

        test.log.info("TEST_STEP1: Do blockcopy")
        virsh_session.sendline(cmd)
        test.log.debug("Blockcopy cmd:%s" % cmd)

        test.log.info("TEST_STEP2: Do blockjob with pivot")
        if not utils_misc.wait_for(
                lambda: libvirt.check_blockjob(vm_name, target_disk,
                                               "progress", "100"), 2):

            result = virsh.blockjob(vm_name, target_disk,
                                    options=pivot_option, debug=True,
                                    ignore_status=True)
            libvirt.check_result(result, expected_fails=err_msg,
                                 check_both_on_error=True)
        else:
            test.error("Please reset bandwidth to test pivot "
                       "before blockcopy finished ")

    def run_delete_copy_file():
        """
        Do blockjob --pivot after deleting the destination copy file.
        """
        test.log.info("TEST_STEP1: Do blockcopy")
        virsh.blockcopy(vm_name, target_disk, tmp_copy_path,
                        blockcopy_options, ignore_status=False,
                        debug=True)
        check_obj.check_mirror_exist(vm, target_disk, tmp_copy_path)

        test.log.info("TEST_STEP2: Delete blockcopy file")
        test_obj.clean_file(tmp_copy_path)

        test.log.info("TEST_STEP3: Do blockjob with pivot")
        virsh.blockjob(vm_name, target_disk, options=pivot_option,
                       debug=True, ignore_status=False)

    def teardown_test():
        """
        Clean data.
        """
        virsh.blockjob(vm_name, target_disk, options=abort_option,
                       debug=True, ignore_status=True)
        test_obj.clean_file(tmp_copy_path)
        bkxml.sync()

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get("target_disk")
    blockcopy_options = params.get("blockcopy_options")
    err_msg = params.get("err_msg")
    pivot_option = params.get("pivot_option")
    abort_option = params.get("abort_option")
    test_scenario = params.get("test_scenario")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)

    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)

    run_test = eval("run_%s" % test_scenario)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
