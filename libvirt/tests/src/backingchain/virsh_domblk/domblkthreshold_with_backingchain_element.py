import re
import time

from virttest import utils_disk
from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Test domblkthreshold for domain which has backing chain
    """
    def setup():
        """
        Prepare active domain and backingchain
        """
        test.log.info("Setup env.")
        test_obj.backingchain_common_setup(create_snap=True,
                                           snap_num=4)

    def test_backing_target():
        """
        Do domblkthreshold for the backing file device target
        """
        test.log.info("TEST_STEP1:Set domblkthreshold for backing file device")
        virsh.domblkthreshold(vm_name, '%s[%s]' % (target_disk, domblk_index),
                              domblk_threshold, debug=True,
                              ignore_status=False)
        check_domstats_threshold(domstats_option, domblk_threshold)

        test.log.info("TEST_STEP2:Do blockcommit and check event")
        event_session = virsh.EventTracker.start_get_event(vm_name)
        write_file()
        virsh.blockcommit(vm.name, target_disk,
                          commit_options % test_obj.snap_path_list[1],
                          ignore_status=False, debug=True)
        expected_event = event % (vm_name, target_disk, domblk_index,
                                  domblk_threshold)
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
        check_domstats_threshold(domstats_option, domblk_threshold)

        test.log.info("TEST_STEP2:Write file in guest and check event")
        event_session = virsh.EventTracker.start_get_event(vm_name)
        write_file()
        expected_event = event % (vm_name, target_disk, domblk_threshold)
        check_event(event_session, expected_event)
        check_domstats_threshold(domstats_option)

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
        if not utils_misc.wait_for(
                lambda:
                bool(threshold_value) == ('threshold' in virsh.domstats(
                    vm_name, options, debug=True).stdout_text.strip()), 30, 2):
            test.fail('Failed to get expected threshold value in 30s')

        result = virsh.domstats(vm_name, options, debug=True,
                                ignore_status=False).stdout_text.strip()
        if not threshold_value:
            pattern = "block.*.threshold"
            if re.search(pattern, result):
                test.fail("Threshold: %s should not be in %s" % (pattern, result))
        else:
            pattern = r"block.*.threshold=%s" % threshold_value
            if not re.search(pattern, result):
                test.fail("Not get correct threshold: %s should be in %s" % (
                    pattern, result))

    def write_file():
        """
        Write file in guest
        """
        session = vm.wait_for_login()
        utils_disk.dd_data_to_vm_disk(session, large_file, bs='4M', count='200')
        session.close()

    def check_event(event_session, expected_event):
        """
        Check event correct

        :param event_session: virsh session
        :param expected_event: expected event pattern
        """
        test.log.debug('Checking event pattern is -> %s', expected_event)
        event_output = virsh.EventTracker.finish_get_event(event_session)
        if not re.search(expected_event, event_output):
            test.fail('Not find: %s from event output:%s' % (
                expected_event, event_output))

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    event = params.get('event')
    case_name = params.get('case_name', '')
    target_disk = params.get('target_disk')
    domblk_threshold = params.get('domblk_threshold')
    domblk_index = params.get('domblk_index')
    domstats_option = params.get('domstats_option')
    commit_options = params.get('commit_options')

    test_obj = blockcommand_base.BlockCommand(test, vm, params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    large_file = '/tmp/file'

    run_test = eval("test_%s" % case_name)

    try:
        setup()
        run_test()

    finally:
        teardown()
