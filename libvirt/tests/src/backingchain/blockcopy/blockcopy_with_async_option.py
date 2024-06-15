import os
import re

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions


def run(test, params, env):
    """
    Do blockcopy with async related option

    1) Prepare disk and snap chain
        disk types: file
    2) Do blockcopy:
         --async
         --async and --timeout option
    3) Check result
    """

    def setup_test():
        """
        Prepare running domain
       """
        test.log.info("Setup env.")
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

    def run_test():
        """
        Do blockcopy with async option
        check backingchain result
        """
        test.log.info("TEST_STEP1: Do blockcopy ")
        virsh_session = _get_session()
        virsh.blockcopy(vm_name, target_disk, test_obj.copy_image,
                        options=blockcopy_options, debug=True)
        _check_res(case, virsh_session)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()

        bkxml.sync()

        if os.path.exists(test_obj.copy_image):
            os.remove(test_obj.copy_image)

    def _check_res(case, virsh_session=None):
        """
        Check expected chain and event output

        :param case: case name
        :param virsh_session: virsh session to get event output
        """
        if case == 'async_timeout':
            test.log.info("TEST_STEP2: Check no blockjob and cancel event")
            if not utils_misc.wait_for(
                    lambda: libvirt.check_blockjob(vm_name, target_disk, "none"), 30):
                test.fail("There should be no current block job")

            event_output = virsh.EventTracker.finish_get_event(virsh_session)
            if not re.search(expected_job, event_output):
                test.fail('Not find: %s from event output:%s' % (expected_job,
                                                                 event_output))

        elif case == 'async':
            def _check_path():
                try:
                    check_obj.check_backingchain_from_vmxml(
                        disk_type, target_disk, [test_obj.copy_image])
                    return True
                except Exception as e:
                    return False

            test.log.info("TEST_STEP2: Check expected chain")
            if not utils_misc.wait_for(_check_path, 60):
                test.fail('Expect source file to be %s' % [test_obj.copy_image])

    def _get_session():
        """
        Get virsh session and confirm join session successfully
        """
        if case == "async_timeout":
            virsh_session = virsh.EventTracker.start_get_event(
                vm_name, event_cmd=event_cmd)
            return virsh_session

        elif case == "async":
            return ''

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get('disk_type')
    case = params.get('case')
    event_cmd = params.get('event_cmd')
    expected_job = params.get('expected_job')
    expected_chain_index = params.get('expected_chain')
    blockcopy_options = params.get('blockcopy_options')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    test_obj.copy_image = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
