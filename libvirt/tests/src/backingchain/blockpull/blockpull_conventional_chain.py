import logging

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Do blockpull for conventional chain.

    1) Prepare different type disk and snap chain
        disk types: file, block, network(rbd with auth)
    2) Do blockpull:
        without --base
        with --base
    3) Check result:
        Check vmxml chain and hash value of new device
    """

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
       """
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test_obj.prepare_snapshot(snap_num=4)

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        LOG.debug('After do snapshots, vmxml is:\n%s', vmxml)

    def run_test():
        """
        Do blockpull and check backingchain result , hash value
        """
        if base_index:
            # with --base option scenario
            base_option = " --base %s" % test_obj.snap_path_list[int(base_index) - 1]
        else:
            # without --base option scenario
            base_option = " "

        session = vm.wait_for_login()
        expected_hash, check_disk = test_obj.get_hash_value(session,
                                                            "/dev/" + test_obj.new_dev)
        virsh.blockpull(vm.name, target_disk, pull_options+base_option,
                        ignore_status=False, debug=True)

        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                expected_chain)
        check_obj.check_hash_list([check_disk], [expected_hash], session)
        session.close()

    def teardown_test():
        """
        Clean data.
        """
        test_obj.backingchain_common_teardown()

        bkxml.sync()
        disk_obj.cleanup_disk_preparation(disk_type)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get('disk_type')
    disk_dict = eval(params.get('disk_dict', '{}'))
    pull_options = params.get('pull_options')
    expected_chain_index = params.get('expected_chain')
    base_index = params.get('base_image_suffix')
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
