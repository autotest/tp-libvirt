import os

from virttest import data_dir
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Do blockcopy with granularity/buf-size options

    1) Prepare disk and snap chain
        disk type: file
    2) Do blockcopy:
        --granularity
        --buf-size
    3) Check result
    """

    def setup_test():
        """
        Prepare running domain and snapshot
        """
        test.log.info("Setup env: prepare running vm and snapshot")
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)
        test_obj.backingchain_common_setup(create_snap=True, snap_num=snap_num)

    def run_test():
        """
        Do blockcopy with granularity/buf-size options
        Check result
        """
        test.log.info("TEST_STEP1: Do blockcopy ")
        blockcopy_options = f'{blockcopy_option} {test_size}'
        test_obj.copy_image = data_dir.get_data_dir() + '/rhel.copy'
        result = virsh.blockcopy(vm_name, target_disk, test_obj.copy_image,
                                 options=blockcopy_options, debug=True)
        _check_result(result)

    def check_libvirt_log():
        """
        Check if expected information can be found in libvirtd log.
        """
        if not os.path.exists(libvirtd_log_file):
            test.fail("Expected VM log file: %s not exists" % libvirtd_log_file)
        result = utils_misc.wait_for(lambda: libvirt.check_logfile(expected_log, libvirtd_log_file), timeout=20)
        if not result:
            test.fail("Can't get expected log %s in %s" % (expected_log, libvirtd_log_file))

    def _check_result(result):
        """
        Check the result
        """
        libvirt.check_exit_status(result, status_error)
        if not status_error:
            check_libvirt_log()
            # Pivot the blockcopy process
            virsh.blockjob(vm_name, target_disk, options=abort_option,
                           debug=True, ignore_status=False)
        else:
            if error_msg:
                libvirt.check_result(result, error_msg)

    def teardown_test():
        """
        Clean the test environment
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        # clean up snapshot
        test_obj.backingchain_common_teardown()
        # clean up disk image and copy image
        test_obj.clean_file(test_obj.new_image_path)
        test_obj.clean_file(test_obj.copy_image)
        # Restore libvirtd conf and restart libvirtd/virtqemud
        libvirtd_conf.restore()
        utils_libvirtd.libvirtd_restart()
        if libvirtd_log_file and os.path.exists(libvirtd_log_file):
            os.unlink(libvirtd_log_file)
        bkxml.sync()

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get("disk_type")
    disk_dict = eval(params.get("disk_dict", "{}"))
    abort_option = params.get("abort_option")
    snap_num = int(params.get("snap_num"))
    expected_log = params.get("expected_log")
    blockcopy_option = params.get("blockcopy_option")
    test_size = params.get("test_size")
    error_msg = params.get("error_msg", "")
    status_error = params.get("status_error", "no") == "yes"

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)

    # Prepare libvirt log
    libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
    libvirtd_conf_dict = {"log_filters": '"3:json 1:libvirt 1:qemu"',
                          "log_outputs": '"1:file:%s"' % libvirtd_log_file}
    libvirtd_conf = libvirt.customize_libvirt_config(libvirtd_conf_dict)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
