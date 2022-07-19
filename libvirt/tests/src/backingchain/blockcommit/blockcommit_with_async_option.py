import re

from virttest import virsh, utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Do blockcommit with async related option

    1) Prepare disk and snap chain
        disk types: file
    2) Do blockcommit:
         --async
         --async and --timeout option
    3) Check result:
        snap chain
    """

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
       """
        test.log.info("Setup env.")

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test_obj.prepare_snapshot(snap_num=4)

    def test_async_timeout():
        """
        Do blockcommit with --async --timeout option
        check backingchain result
        """
        test.log.info("TEST_STEP1: Get option ")
        base_option = " --base %s" % test_obj.snap_path_list[int(base_index)-1]

        test.log.info("TEST_STEP2: Do blockcommit ")
        virsh_session = _get_session()
        virsh.blockcommit(vm.name, target_disk, base_option+commit_options, debug=True)

        _check_res(case, virsh_session)

    def test_async():
        """
        Do blockcommit with async option
        check backingchain result
        """
        test.log.info("TEST_STEP1: Get option ")
        base_option = " --base %s" % test_obj.snap_path_list[int(base_index)-1]

        test.log.info("TEST_STEP2: Do blockcommit ")
        virsh.blockcommit(vm.name, target_disk, commit_options+base_option+top_option,
                          ignore_status=False, debug=True)
        virsh.blockjob(vm_name, test_obj.new_dev, options=' --pivot',
                       debug=True, ignore_status=False)
        _check_res(case)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()

        bkxml.sync()
        disk_obj.cleanup_disk_preparation(disk_type)

    def _check_res(case, virsh_session=None):
        """
        Check expected chain and event output

        :param case: case name
        :param virsh_session: virsh session to get event output
        """

        if case == 'async_timeout':
            if not utils_misc.wait_for(
                    lambda: expected_job in virsh_session.get_stripped_output(),
                    10, step=0.01):
                test.fail('Not find %s in event output:%s' % (
                    expected_job, virsh_session.get_stripped_output()))

        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, target_disk,
                                                expected_chain)

    def _get_session():
        """
        Get virsh session and confirm join session successfully
        """
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(event_cmd % vm_name)

        if not utils_misc.wait_for(
                lambda: re.search("Welcome to virsh",
                                  virsh_session.get_stripped_output()), timeout=10, first=5):
            test.error('Join virsh session failed')

        return virsh_session

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get('disk_type')
    commit_options = params.get('commit_options')
    base_index = params.get('base_image_suffix')
    case = params.get('case')
    event_cmd = params.get('event_cmd')
    expected_job = params.get('expected_job')
    expected_chain_index = params.get('expected_chain')
    top_option = params.get('top_option')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    test_obj.new_image_path = libvirt_disk.get_first_disk_source(vm)

    run_test = eval("test_%s" % case)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
