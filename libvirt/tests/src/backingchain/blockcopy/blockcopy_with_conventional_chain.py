import os

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test blockcopy with conventional chain.

    1) Prepare an running guest.
    2) Create snap.
    3) Do blockcopy with shallow/no shallow.
    4) Check hash value and chain value.
    """

    def setup_test():
        """
        Prepare expected disk type, snapshots, blockcopy path.
        """
        test.log.info("TEST_SETUP:Prepare new disk and snapchain.")
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        test_obj.prepare_snapshot(snap_num=snap_num,
                                  extra=snap_extra)
        test_obj.clean_file(tmp_copy_path)

    def run_test():
        """
        Do blockcopy and abort job ,than check hash and vmxml chain
        """
        session = vm.wait_for_login()
        expected_hash = test_obj.get_hash_value(session,
                                                "/dev/" + test_obj.new_dev,
                                                sleep_time=20)

        test.log.info("TEST_STEP1: Do blockcopy and abort job")
        virsh.blockcopy(vm_name, target_disk, tmp_copy_path,
                        options=blockcopy_options, ignore_status=False,
                        debug=True)

        test.log.info("TEST_STEP2: Check expected chain and hash value")
        check_obj.check_mirror_exist(vm, target_disk, tmp_copy_path)

        if not utils_misc.wait_for(
                lambda: libvirt.check_blockjob(vm_name, target_disk, "progress", "100(.00)?"), 30):
            test.fail("Blockcopy not finished for 30s")
        test.log.info("TEST_STEP3: Abort job with %s", execute_option)
        virsh.blockjob(vm_name, target_disk, execute_option,
                       ignore_status=False, debug=True)

        test.log.info("TEST_STEP4: Check expected chain and hash value ")
        test_obj.copy_image = tmp_copy_path
        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, target_disk, expected_chain)

        check_obj.check_hash_list(["/dev/" + test_obj.new_dev], [expected_hash],
                                  session, sleep_time=20)
        session.close()

    def teardown_test():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()
        bkxml.sync()
        disk_obj.cleanup_disk_preparation(disk_type)

        if os.path.exists(tmp_copy_path):
            os.remove(tmp_copy_path)
        if os.path.exists(test_obj.new_image_path):
            os.remove(test_obj.new_image_path)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    target_disk = params.get('target_disk')
    disk_type = params.get("disk_type")
    disk_dict = eval(params.get('disk_dict', '{}'))
    execute_option = params.get("execute_option")
    blockcopy_options = params.get('blockcopy_option')
    snap_extra = params.get("snap_extra", "")
    snap_num = int(params.get("snap_num", 4))
    expected_chain_index = params.get("expected_chain", "")

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    # Get vm xml
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)
    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)

    try:
        # Execute test
        setup_test()
        run_test()

    finally:
        teardown_test()
