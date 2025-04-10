import re
import time

from virttest import utils_disk
from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml

from provider.backingchain import blockcommand_base
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test domblkthreshold for domain which has backing chain
    """
    def setup():
        """
        Prepare active domain and backingchain
        """
        test.log.info("Setup env.")
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict,
                                                       size=disk_size)
        test_obj.backingchain_common_setup(create_snap=True,
                                           snap_num=4, extra=snap_extra)
        test.log.debug("Preparing xml is:\n %s", vm_xml.VMXML.new_from_dumpxml(vm.name))

    def test_backing_target():
        """
        Do domblkthreshold for the backing file device target
        """
        test.log.info("TEST_STEP1-2:Set domblkthreshold for backing file device")
        time.sleep(5)
        virsh.domblkthreshold(vm_name, '%s[%s]' % (target_disk, domblk_index),
                              domblk_threshold, debug=True,
                              ignore_status=False)
        check_domstats_threshold(domstats_option, actual_threshold)

        test.log.info("TEST_STEP3-4:Write file and check event")
        expected_event = event % (vm_name, target_disk, domblk_index,
                                  actual_threshold)
        event_session = virsh.EventTracker.start_get_event(vm_name)
        write_file()
        check_event(event_session, expected_event, existed=False)

        test.log.info("TEST_STEP5-7:Do blockcommit and check event")
        virsh.blockcommit(vm.name, target_disk,
                          commit_options % test_obj.snap_path_list[0],
                          ignore_status=False, debug=True)
        check_event(event_session, expected_event)
        check_domstats_threshold(domstats_option)

    def test_entire_disk():
        """
        Do domblkthreshold for the entire disk device target
        """
        test.log.info("TEST_STEP1:Set domblkthreshold for the entire disk")
        time.sleep(5)
        virsh.domblkthreshold(vm_name, '%s' % target_disk,
                              domblk_threshold, debug=True,
                              ignore_status=False)
        check_domstats_threshold(domstats_option, actual_threshold)

        test.log.info("TEST_STEP2:Write file in guest and check event")
        event_session = virsh.EventTracker.start_get_event(vm_name)
        write_file()
        expected_event = event % (vm_name, target_disk, actual_threshold)
        check_event(event_session, expected_event)

    def teardown():
        """
        Clean env
        """
        test_obj.backingchain_common_teardown()

        session = vm.wait_for_login()
        test_obj.clean_file(large_file, session)
        session.close()

        bkxml.sync()

    def check_domstats_threshold(options, threshold_value=None):
        """
        Check domstats threshold

        :param options: extra options for virsh domstats command
        :param threshold_value: domstats threshold value, if it is None,
        result should be no output
        """
        if not threshold_value:
            result = virsh.domstats(vm_name, options, debug=True,
                                    ignore_status=False).stdout_text.strip()
            pattern = "block.*.threshold"
            if re.search(pattern, result):
                test.fail("Threshold: %s should not be in %s" % (pattern, result))
        else:
            if not utils_misc.wait_for(
                    lambda:
                    bool(threshold_value) == ('threshold' in virsh.domstats(
                        vm_name, options, debug=True).stdout_text.strip()), 30, 2):
                test.fail('Failed to get expected threshold value in 30s')
            result = virsh.domstats(vm_name, options, debug=True,
                                    ignore_status=False).stdout_text.strip()
            pattern = r"block.*.threshold=%s" % threshold_value
            if not re.search(pattern, result):
                test.fail("Not get correct threshold: %s should be in %s" % (
                    pattern, result))

    def write_file():
        """
        Write file in guest
        """
        session = vm.wait_for_login()
        cmd = "mkfs.ext4 /dev/%s;mount /dev/%s /mnt" % (target_disk, target_disk)
        session.cmd_status_output(cmd)
        utils_disk.dd_data_to_vm_disk(session, mount_file, bs='8M', count='400')
        session.close()

    def check_event(event_session, expected_event, existed=True):
        """
        Check event correct

        :param event_session: virsh session
        :param expected_event: expected event pattern
        :param existed: if event existed, default True
        """
        test.log.debug('Checking event pattern is -> %s', expected_event)
        event_output = event_session.get_stripped_output()
        search_res = re.search(expected_event, event_output)
        if existed:
            if not search_res:
                test.fail('Not find: %s from event output:%s' % (
                    expected_event, event_output))
        else:
            if search_res:
                test.fail('Event:%s should not exist in output:%s' % (
                    expected_event, event_output))
        test.log.debug("Checking event successfully")

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    event = params.get('event')
    case_name = params.get('case_name', '')
    target_disk = params.get('target_disk')
    domblk_threshold = params.get('domblk_threshold')
    actual_threshold = params.get('actual_threshold')
    domblk_index = params.get('domblk_index')
    domstats_option = params.get('domstats_option')
    commit_options = params.get('commit_options')
    disk_dict = eval(params.get('disk_dict', '{}'))
    disk_type = params.get("disk_type")
    disk_size = params.get("disk_size")
    mount_file = params.get("mount_file", "/mnt/file")
    snap_extra = params.get("snap_extra")

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    large_file = '/tmp/file'

    run_test = eval("test_%s" % case_name)

    try:
        setup()
        run_test()

    finally:
        teardown()
