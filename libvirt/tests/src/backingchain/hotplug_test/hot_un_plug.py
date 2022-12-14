from virttest import virsh
from virttest import data_dir
from virttest.libvirt_xml import vm_xml

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Do blockcommit/blockpull/blockcopy after hot-unplug and hotplug the disk

    1) Prepare a running guest and snap chain:
        disk types: file
    2) Hot-unplug and hotplug the testing disk.
    3) Do blockcommit/blockpull/blockpull.
    4) Check result:
        Hotplug and hot-unplug testing disk successfully.
        Get the expected chain
    """

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
        """
        test.log.info("Setup env: prepare a running guest and snap chain.")
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)
        test_obj.backingchain_common_setup(create_snap=True, snap_num=snap_num, extra=snap_extra)
        check_domblklist(target_disk)

    def run_test():
        """
        Hot-unplug and hotplug the testing disk.
        Do blockcommit/blockpull/blockcopy.
        Check results.
        """
        test.log.info("TEST_STEP1: Hot-unplug the disk and hotplug it again.")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        diskxml = vmxml.devices.by_device_tag('disk')[1]
        virsh.detach_disk(vm_name, target_disk, ignore=False, debug=True,
                          wait_for_event=True)
        check_domblklist(target_disk, with_target_disk=False)
        virsh.attach_device(vm_name, diskxml.xml, ignore=False, debug=True)
        check_domblklist(target_disk)
        test.log.info("TEST_STEP2: Do %s and check dumpxml." % block_cmd)
        block_operation()

    def teardown_test():
        """
        Clean up the test environment.
        """
        # Clean up the snapshots related env
        test_obj.backingchain_common_teardown()
        # Clean up the created new image
        disk_obj.cleanup_disk_preparation(disk_type)
        # Clean up the copy image for blockcopy
        if block_cmd == "blockcopy":
            test_obj.clean_file(test_obj.copy_image)
        bkxml.sync()

    def block_operation():
        """
        Do blockcommit/blockpull/blockpull.
        """
        if block_cmd == "blockcommit":
            top_option = " --top %s" % test_obj.snap_path_list[int(top_index) - 1]
            base_option = " --base %s" % test_obj.snap_path_list[int(base_index) - 1]
            virsh.blockcommit(vm_name, target_disk, top_option+base_option+common_options,
                              ignore_status=False, debug=True)
        elif block_cmd == "blockpull":
            base_option = " --base %s" % test_obj.snap_path_list[int(base_index) - 1]
            virsh.blockpull(vm_name, target_disk, base_option+common_options,
                            ignore_status=False, debug=True)
        elif block_cmd == "blockcopy":
            test_obj.copy_image = data_dir.get_data_dir() + '/rhel.copy'
            virsh.blockcopy(vm_name, target_disk, test_obj.copy_image, blockcopy_option,
                            ignore_status=False, debug=True)
        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, target_disk, expected_chain)

    def check_domblklist(target_disk, with_target_disk=True):
        """
        Check domblklist for the guest.
        :params target_disk: the disk we want to check
        :params with_target_disk: check if the disk is in domblklist
        """
        domblklist_result = virsh.domblklist(vm_name, debug=True).stdout_text.strip()
        if with_target_disk:
            if target_disk not in domblklist_result:
                test.fail("Can't get expected disk %s in guest." % target_disk)
        else:
            if target_disk in domblklist_result:
                test.fail("The target disk %s can't be detached in guest." % target_disk)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get("disk_type")
    snap_num = int(params.get("snap_num"))
    common_options = params.get("common_options")
    top_index = params.get("top_image_suffix")
    base_index = params.get("base_image_suffix")
    expected_chain_index = params.get("expected_chain")
    disk_dict = eval(params.get("disk_dict", {}))
    block_cmd = params.get("block_cmd")
    snap_extra = params.get("snap_extra")
    blockcopy_option = params.get("blockcopy_option")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
