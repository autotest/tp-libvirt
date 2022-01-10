import logging

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions

LOG = logging.getLogger('avocado.backingchain.checkfunction')


def run(test, params, env):
    """
    Case for RHEL-288322
    Test blockcommit operation after creating disk-only snapshot.

    1) Prepare block type disk and snap chain
    2) Test blockcommit operation from top to base.
    3) Check result.
    """

    def setup_commit_top_to_base():
        """
        Prepare specific type disk and create snapshots.
        """
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
        test_obj.update_disk()
        test_obj.prepare_snapshot()
        check_obj.check_backingchain(test_obj.snap_path_list[::-1])

    def test_commit_top_to_base():
        """
        Do blockcommit from top to base after creating external
        disk-only snapshot with specific disk type
        """
        commit_options = " --top %s --pivot " % (test_obj.snap_path_list[-1])
        virsh.blockcommit(vm.name, test_obj.new_dev, commit_options,
                          ignore_status=False, debug=True)
        # Check result after block commit
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        check_obj.check_block_operation_result(vmxml, 'blockcommit',
                                               test_obj.new_dev,
                                               test_obj.snap_path_list[::-1] +
                                               [test_obj.src_path])

    def teardown_commit_top_to_base():
        """
        Clean data.
        """
        LOG.info('Start cleaning up.')
        for ss in test_obj.snap_name_list:
            virsh.snapshot_delete(vm_name, '%s --metadata' % ss, debug=True)
        for sp in test_obj.snap_path_list:
            process.run('rm -rf %s' % sp)
        bkxml.sync()
        libvirt.setup_or_cleanup_iscsi(is_setup=False)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case_name = params.get('case_name', '')
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    # MAIN TEST CODE ###
    run_test = eval("test_%s" % case_name)
    setup_test = eval("setup_%s" % case_name)
    teardown_test = eval("teardown_%s" % case_name)

    try:
        # Execute test
        setup_test()
        run_test()

    finally:
        teardown_test()
