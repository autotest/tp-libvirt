import logging

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test blockresize for domain which has backing chain element.

    """

    def setup_raw_disk_blockresize():
        """
        Prepare raw disk and create snapshots.
        """
        # Create raw type image
        image_path = test_obj.tmp_dir + '/blockresize_test'
        libvirt.create_local_disk("file", path=image_path, size='500K',
                                  disk_format="raw")
        test_obj.new_image_path = image_path
        # attach new disk
        virsh.attach_disk(vm.name, source=image_path, target=device,
                          extra=extra, debug=True)
        test_obj.new_dev = device
        # create snap chain
        test_obj.prepare_snapshot()

    def test_raw_disk_blockresize():
        """
        Test blockresize for raw type device which has backing chain element.
        """
        new_size = params.get('expected_block_size')
        result = virsh.blockresize(vm_name, test_obj.snap_path_list[-1],
                                   new_size, debug=True)
        libvirt.check_exit_status(result)
        check_obj.check_image_info(test_obj.snap_path_list[-1], 'vsize', new_size)

    def teardown_raw_disk_blockresize():
        """
        Clean env and resize with origin size.
        """
        # clean new disk file
        test_obj.backingchain_common_teardown()
        # detach disk
        virsh.detach_disk(vm_name, target=test_obj.new_dev,
                          wait_for_event=True, debug=True)
        # clean image file
        process.run('rm -f %s' % test_obj.new_image_path)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case_name = params.get('case_name', '')
    device = params.get('new_disk')
    extra = params.get('attach_disk_extra_options')
    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    # Get vm xml
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)

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
        bkxml.sync()
