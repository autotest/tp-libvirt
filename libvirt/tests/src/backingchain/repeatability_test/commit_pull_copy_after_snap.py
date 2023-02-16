from virttest import virsh                            # pylint: disable=W0611
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Take turn to create snapshots and do blockcommit/blockpull/blockcopy repeatedly
    1. Create snapshot for the running guest.
    2. Do blockcommit/blockpull/blockcopy.
    3. Delete snapshot and its image file.
    4. Repeat above steps for 5 times.
    """

    def setup_test():
        """
        Create snapshot for the running guest
        """
        test.log.info("Setup env: prepare a running guest and snap chain.")
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)
        test_obj.backingchain_common_setup(create_snap=True, snap_num=snap_num,
                                           extra=snap_extra)

    def run_test():
        """
        Create snapshots and do blockcommit/blockpull/blockcopy
        """
        setup_test()
        test.log.info("TEST_STEP1: Do %s for the guest", block_cmd)
        block_operation()
        test.log.info("TEST_STEP2: Delete the snapshot and restore guest")
        teardown_test()

    def teardown_test():
        """
        Clean up the test environment
        """
        # Clean up the snapshots
        libvirt.clean_up_snapshots(vm_name)
        # Clean up the copy image
        test_obj.clean_file(copy_image)
        bkxml.sync()

    def block_operation():
        """
        Do blockcommit/blockpull/blockcopy and delete snapshot repeatedly
        """
        block_options = common_option + block_option
        blockcmds = eval(f'virsh.{block_cmd}')
        res = blockcmds(vm_name, target_disk, block_options,
                        ignore_status=False, debug=True)
        libvirt.check_exit_status(res, status_error)

    vm_name = params.get("main_vm")
    target_disk = params.get("target_disk")
    common_option = params.get("common_option", "")
    block_option = params.get("block_option", "")
    snap_num = int(params.get("snap_num"))
    block_cmd = params.get("block_cmd")
    status_error = ("yes" == params.get("status_error", "no"))
    disk_dict = eval(params.get("disk_dict", {}))
    disk_type = params.get("disk_type")
    snap_extra = params.get("snap_extra")
    copy_image = params.get("copy_image", "")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        for i in range(5):
            run_test()
    finally:
        test.log.info("Create snapshot and do %s repeated sucessfully", block_cmd)
