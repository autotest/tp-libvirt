import logging

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions

LOG = logging.getLogger('avocado.backingchain.checkfunction')


def run(test, params, env):
    """
    Case for RHEL-288322
    Test blockcommit operation after creating disk-only snapshot.

    1) Prepare different type disk and snap chain
        file type: file, block, volume, nfs, rbd with auth
    2) Do blockcommit:
        from middle to middle
        from top to base
        from top to middle
        from middle to base	.
    3) Check result.
    """

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
       """
        test_obj.update_disk(disk_type, disk_dict)

        if not vm.is_alive():
            vm.start()
        test_obj.prepare_snapshot(snap_num=4)

    def run_test():
        """
        Do blockcommit from top to base after creating external
        disk-only snapshot with specific disk type
        """

        # top_option = test_obj.snap_path_list[int(top_index)-1] if top_index else test_obj.snap_path_list[-1]
        # base_option = test_obj.snap_path_list[int(base_index)-1] if base_index else original_disk_source
        # options = commit_options + " --top %s --base %s"\
        #           % (top_option, base_option)

        top_option = " --top %s" % test_obj.snap_path_list[int(top_index)-1]\
            if top_index else " --pivot"
        base_option = " --base %s" % test_obj.snap_path_list[int(base_index)-1]\
            if base_index else " --pivot"

        virsh.blockcommit(vm.name, target_disk, top_option+base_option+commit_options,
                          ignore_status=False, debug=True)

        expected_chain = []
        for i in expected_chain_index.split('>'):
            if i == "base":
                expected_chain.append(original_disk_source)
            else:
                expected_chain.append(test_obj.snap_path_list[int(i)-1])
        check_obj.check_backingchain_from_vmxml(test_obj.new_dev, expected_chain)

        if not vm.is_alive():
            test.fail("vm state isn't alive after blockcommit, but:%s", vm.state)

    def teardown_test():
        """
        Clean data.
        """
        test_obj.backingchain_common_teardown()
        bkxml.sync()

        if disk_type == 'block':
            libvirt.setup_or_cleanup_iscsi(is_setup=False)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    disk_type = params.get('disk_type')
    disk_dict = eval(params.get('disk_dict', '{}'))

    commit_options = params.get('commit_options')
    expected_chain_index = params.get('expected_chain')
    top_index = params.get('top_index')
    base_index = params.get('base_index')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    original_disk_source = libvirt_disk.get_first_disk_source(vm)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()


