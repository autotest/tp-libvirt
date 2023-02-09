from virttest import virsh
from virttest import data_dir
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Restart libvirtd and check xml during mirror phase of blockcommit:
    1) Prepare a running guest with snapshots.
    2) Do active blockcommit/blockcopy to the guest.
    3) Restart the libvirtd/virtqemud service.
    4) Check the mirror element in disk xml.
    """

    def setup_test():
        """
        Prepare a running guest with snapshots and do blockcommit/blockcopy.
        """
        test.log.info("SETUP_STEP1: prepare a running guest and snap chain.")
        test_obj.backingchain_common_setup(create_snap=True, snap_num=snap_num)
        test.log.info("SETUP_STEP2: Do active %s to the guest", block_cmd)
        if block_cmd == "blockcommit":
            virsh.blockcommit(vm_name, target_disk, block_options,
                              ignore_status=False, debug=True)
        else:
            test_obj.copy_image = data_dir.get_data_dir() + '/rhel.copy'
            virsh.blockcopy(vm_name, target_disk, test_obj.copy_image, block_options,
                            ignore_status=False, debug=True)

    def run_test():
        """
        Restart the libvirtd/virtqemud service.
        Check the mirror element in disk xml.
        """
        # Get the mirror xml before restart service
        test.log.info("TEST_STEP1: Check the mirror xml before restart service")
        pre_mirror = utils_misc.wait_for(lambda: libvirt_disk.get_mirror_part_in_xml(vm, target_disk),
                                         timeout=9, first=5, step=1)
        test.log.debug("The mirror xml before restart service: %s", pre_mirror)

        test.log.info("TEST_STEP2: Restart service")
        utils_libvirtd.libvirtd_restart()
        # Check the status of guest after restart service
        if not libvirt.check_vm_state(vm_name, "running"):
            test.fail("The guest status changed after restart service")

        test.log.info("TEST_STEP3: Check the mirror xml after restart service")
        post_mirror = libvirt_disk.get_mirror_part_in_xml(vm, target_disk)
        test.log.debug("The mirror xml after restart service: %s", post_mirror)
        if pre_mirror != post_mirror:
            test.fail("The mirror xml in disk changed after restart service")

        test.log.info("TEST_STEP4: Pivot the blockjob")
        virsh.blockjob(vm_name, target_disk, " --pivot",
                       ignore_status=False, debug=True)

    def teardown_test():
        """
        Clean up the test environment.
        """
        # Clean up the snapshots
        test_obj.backingchain_common_teardown()
        # Clean up the copy image for blockcopy
        test_obj.clean_file(test_obj.copy_image)
        bkxml.sync()

    vm_name = params.get("main_vm")
    target_disk = params.get("target_disk")
    snap_num = int(params.get("snap_num"))
    block_options = params.get("block_options", "")
    block_cmd = params.get("block_cmd")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    test_obj = blockcommand_base.BlockCommand(test, vm, params)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
