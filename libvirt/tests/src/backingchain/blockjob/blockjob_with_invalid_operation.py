import os

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Check blockjob after invalid operations

    1) Prepare active guest
    2) Do blockjob:
        a.Check blockjob for a non-existing disk path.
        b.Check blockjob after blockcopy fails when the target file is
        less than source image.
    """

    def setup_test():
        """
        Prepare active guest.
        """
        test.log.info("TEST_SETUP:Start vm.")
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

    def run_test_not_existing_path():
        """
        Check blockjob for a non-existing disk path.
        """
        test.log.info("TEST_STEP: Do blockjob ")
        cmd_result = virsh.blockjob(vm_name, path, debug=True)
        libvirt.check_result(cmd_result, err_msg)

    def run_test_release_job():
        """
        Check blockjob after blockcopy fails when the target file is
        less than source image.
        """
        test.log.info("TEST_STEP1: Create target file less than source file")
        libvirt.create_local_disk("file", tmp_copy_path, size)

        test.log.info("TEST_STEP2: Do failed blockcopy with target file ")
        result = virsh.blockcopy(vm_name, target_disk, "",
                                 options=blockcopy_option % tmp_copy_path,
                                 debug=True, ignore_status=True)
        libvirt.check_result(result, blockcopy_err)

        test.log.info("TEST_STEP3: Check no blockjob running")
        if not libvirt.check_blockjob(vm_name, target_disk, "none"):
            test.fail("There should be no blockjob after blockcopy failed")

        test.log.info("TEST_STEP4: Blockcopy should failed again")
        result = virsh.blockcopy(vm_name, target_disk, "",
                                 options=blockcopy_option % tmp_copy_path,
                                 debug=True, ignore_status=True)
        libvirt.check_result(result, blockcopy_err)

    def teardown_test():
        """
        Clean data.
        """
        if os.path.exists(test_obj.new_image_path):
            os.remove(test_obj.new_image_path)
        bkxml.sync()

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    test_scenario = params.get("test_scenario")
    path = params.get("path")
    target_disk = params.get("target_disk")
    size = params.get("less_image_size", "10M")
    err_msg = params.get("err_msg")
    blockcopy_err = params.get("blockcopy_err")
    blockcopy_option = params.get("blockcopy_option")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)
    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)

    run_test = eval("run_test_%s" % test_scenario)
    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
