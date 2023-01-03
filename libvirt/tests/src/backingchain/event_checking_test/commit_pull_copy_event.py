import logging

from virttest import data_dir
from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Check events when doing blockcommit/blockpull/blockcopy

    1) Prepare a running guest with a snapshot.
    2) Do blockcommit/blockpull/blockcopy.
    3) Check the events.
    """

    def setup_test():
        """
        Prepare a running guest with a snapshot
        """
        test.log.info("Setup env: prepare a running guest.")
        test_obj.backingchain_common_setup(create_snap=True, snap_num=snap_num)

    def run_test():
        """
        Do blockcommit/blockpull/blockcopy and check events
        """
        test.log.info("TEST_STEP1: run virsh event command")
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(event_cmd % vm_name)

        def _check_events():
            event_result = virsh_session.get_stripped_output()
            if not utils_misc.wait_for(
                    lambda: expected_event[index] in event_result, 10):
                test.fail("Can't find '%s' in virsh event" % expected_event[index])
            else:
                logging.debug("Can find '%s' in virsh event" % expected_event[index])

        test.log.info("TEST_STEP2: do blockcommit/blockpull/blockcopy")
        blk_test()

        test.log.info("TEST_STEP3: check events in %s" % block_cmd)
        index = 0
        _check_events()

        if block_cmd != "blockpull":
            test.log.info("TEST_STEP4: do blockjob and check events")
            virsh.blockjob(vm_name, target_disk, "--pivot",
                           ignore_status=False, debug=True)
            index = 1
            _check_events()

    def teardown_test():
        """
        Clean up the test environments.
        """
        # Clean up the copy image if exist
        if block_cmd == "blockcopy":
            test_obj.clean_file(test_obj.copy_image)
        # Clean up the snapshot
        test_obj.backingchain_common_teardown()
        bkxml.sync()

    def blk_test():
        """
        Do blockcommit/blockpull/blockcopy.
        """
        blk_options = block_options + special_options
        if block_cmd == "blockcopy":
            test_obj.copy_image = data_dir.get_data_dir() + '/rhel.copy'
            virsh.blockcopy(vm_name, target_disk, test_obj.copy_image,
                            blk_options, ignore_status=False, debug=True)
        else:
            func = virsh.blockcommit if block_cmd == 'blockcommit' else virsh.blockpull
            func(vm_name, target_disk, blk_options, ignore_status=False, debug=True)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get("disk_type")
    snap_num = int(params.get("snap_num"))
    blockcommit_options = params.get("blockcommit_options")
    blockcopy_options = params.get("blockcopy_options")
    block_options = params.get("block_options")
    event_cmd = params.get("event_cmd")
    block_cmd = params.get("block_cmd")
    special_options = params.get("special_options")
    expected_event = eval(params.get("expected_event"))

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
