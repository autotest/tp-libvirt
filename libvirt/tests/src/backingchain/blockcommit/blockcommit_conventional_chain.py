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
    Do blockcommit for conventional chain.

    1) Prepare different type disk and snap chain
        disk types: file, block, volume, file(nfs), network(rbd with auth)
    2) Do blockcommit:
        from middle to middle
        from top to base
        from top to middle
        from middle to base
    3) Check result:
        hash value of file in vm are correct
        vm is alive
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
        check_obj.check_backingchain(test_obj.snap_path_list[::-1])

    def run_test():
        """
        Do blockcommit and check backingchain result , file hash value
        """
        top_option = " --top %s" % test_obj.snap_path_list[int(top_index)-1]\
            if top_index else " --pivot"
        if base_index:
            # this scenario is from middle to middle and from top to middle
            base_option = " --base %s" % test_obj.snap_path_list[int(base_index) - 1]
        elif top_option.split()[-1] == test_obj.snap_path_list[-1]:
            # this scenario is from top to base
            base_option = " --pivot"
        else:
            # this scenario is from middle to base
            base_option = " "

        session = vm.wait_for_login()

        status, _ = session.cmd_status_output("which sha256sum")
        if status:
            test.error("Not find sha256sum command on guest.")
        ret, expected_hash = session.cmd_status_output("sha256sum %s" %
                                                       test_obj.new_dev)

        virsh.blockcommit(vm.name, target_disk,
                          commit_options+top_option+base_option,
                          ignore_status=False, debug=True)

        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                expected_chain)
        check_obj.check_hash_list([test_obj.new_dev], [expected_hash], session)
        session.close()

        if not vm.is_alive():
            test.fail("The vm should be alive after blockcommit, "
                      "but it's in %s state.", vm.state)

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
    commit_options = params.get('commit_options')
    expected_chain_index = params.get('expected_chain')
    top_index = params.get('top_image_suffix')
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
