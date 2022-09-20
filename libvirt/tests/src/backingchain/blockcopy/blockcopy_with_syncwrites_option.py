import os

from virttest import libvirt_version
from virttest import utils_disk
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test blockcopy with --synchronous-writes option.

    1) Prepare a running guest.
    2) Create snap.
    3) Do blockcopy.
    4) Check status by 'qemu-img info'.
    """

    def setup_test():
        """
        Start domain and clean exist copy file
        """
        test.log.info("TEST_SETUP: Prepare disk and running domain.")
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)
        test_obj.backingchain_common_setup(remove_file=True,
                                           file_path=tmp_copy_path)

    def run_test():
        """
        Test blockcopy with --synchronous-writes option.
        """
        test.log.info("TEST_STEP1: Do blockcopy")
        virsh.blockcopy(vm_name, device, tmp_copy_path,
                        options=blockcopy_options,
                        ignore_status=False, debug=True)

        test.log.info("TEST_STEP2: Check mirror and guest write file")
        check_obj.check_mirror_exist(vm, device, tmp_copy_path)
        _check_guest_write_file()

    def teardown_test():
        """
        Clean env
        """
        test_obj.clean_file(tmp_copy_path)
        bkxml.sync()

    def _check_guest_write_file():
        """
        Check guest write file successfully after blockcopy and pivot

        1) Check guest write file
        2) Do blockjob with pivot and check source file
        3) Check guest write file after abort
        """
        session = vm.wait_for_login()
        utils_disk.dd_data_to_vm_disk(session, device)
        session.close()

        virsh.blockjob(vm_name, device, options=abort_option,
                       debug=True, ignore_status=False)
        current_source = list(vm.get_disk_devices().values())[1]['source']
        if current_source != tmp_copy_path:
            test.fail("Current source: %s is not same as original blockcopy"
                      " path:%s" % (current_source, tmp_copy_path))

        session = vm.wait_for_login()
        utils_disk.dd_data_to_vm_disk(session, device)
        session.close()

    libvirt_version.is_libvirt_feature_supported(params)
    utils_misc.is_qemu_function_supported(params)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    device = params.get('target_disk')
    blockcopy_options = params.get('blockcopy_option')
    abort_option = params.get('abort_option')
    disk_type = params.get('disk_type')
    disk_dict = eval(params.get('disk_dict', '{}'))

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)

    try:
        # Execute test
        setup_test()
        run_test()

    finally:
        teardown_test()
