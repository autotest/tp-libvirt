import re

from virttest import virsh

from virttest.libvirt_xml import vm_xml

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Do blockpull with async option

    1) Prepare disk and snap chain
        disk types: file
    2) Do blockpull:
         --async and --timeout option
    3) Check result:
        disk hash value
    """

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
        """
        test.log.info("Setup env.")
        test_obj.backingchain_common_setup(create_snap=True,
                                           snap_num=snap_num)

    def run_test():
        """
        Do blockpull with --async --timeout option
        check backingchain result
        """
        test.log.info("TEST_STEP: Do blockpull and check event")
        virsh_session = virsh.EventTracker.start_get_event(vm_name, event_cmd=event_cmd)
        virsh.blockpull(vm.name, target_disk, base_option+pull_options,
                        debug=True)
        event_output = virsh.EventTracker.finish_get_event(virsh_session)
        if not re.search(expected_job, event_output):
            test.fail('Not find: %s from event output:%s' % (expected_job,
                                                             event_output))

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()
        bkxml.sync()

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    snap_num = int(params.get('snap_num'))
    pull_options = params.get('pull_options')
    base_option = params.get('base_option', '')
    event_cmd = params.get('event_cmd')
    expected_job = params.get('expected_job')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    file_name = "/tmp/big_data"

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
