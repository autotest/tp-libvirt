import logging

from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Do blockcommit when the guest image has backing file

    1) Prepare different type disk and snap chain
        File disk image which has file backing file
        File disk image which has block backing file
        Block disk image which has file backing file
        Block disk image which has block backing file
    2) Do blockcommit:
        from middle to middle
        from top to base
        from top to middle
        from middle to base
    3) Check result:
        md5 of file in vm are correct
        vm is alive
    """
    def _prepare_backing_file():
        """
        Prepare backing file
        """
        based_image, backing_file = '', ''
        if disk_type == 'file':
            based_image = data_dir.get_data_dir()+'/based.qcow2'
            if backing_file_type == 'file':
                backing_file = data_dir.get_data_dir()+'/backing.qcow2'
                libvirt.create_local_disk('file', backing_file,
                                          size='200M', disk_format="qcow2")
            if backing_file_type == 'block':
                backing_file = disk_obj.create_lvm_disk_path(vg0, lv1)

        elif disk_type == 'block':
            based_image = disk_obj.create_lvm_disk_path(vg0, lv0)
            if backing_file_type == 'file':
                backing_file = data_dir.get_data_dir() + '/backing.qcow2'
                libvirt.create_local_disk('file', backing_file, '200M', "qcow2")
            if backing_file_type == 'block':
                backing_file = libvirt.create_local_disk(
                    "lvm", size="10M", vgname=vg0, lvname=lv1)

        backing_cmd = "qemu-img create -f qcow2 -b %s -F %s %s" % (
            backing_file, backing_format, based_image)
        process.run(backing_cmd, shell=True, verbose=True)
        return based_image, backing_file

    def setup_test():
        """
        Prepare specific type disk and create snapshots.
       """
        based_image, test_obj.backing_file = _prepare_backing_file()

        test_obj.new_image_path = disk_obj.add_vm_disk(
            disk_type, disk_dict, new_image_path=based_image)

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test_obj.prepare_snapshot(snap_num=4)
        check_obj.check_backingchain(test_obj.snap_path_list[::-1])

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        LOG.debug("After create snapshots ,the vmxml is:\n%s", vmxml)

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
                                                       "/dev/"+test_obj.new_dev)
        if ret:
            test.error("Get sha256sum value failed")

        virsh.blockcommit(vm.name, target_disk,
                          commit_options+top_option+base_option,
                          ignore_status=False, debug=True)

        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                expected_chain)
        check_obj.check_hash_list(["/dev/"+test_obj.new_dev], [expected_hash], session)
        session.close()

        if not vm.is_alive():
            test.fail("The vm should be alive after blockcommit, "
                      "but it's in %s state." % vm.state)

    def teardown_test():
        """
        Clean data.
        """
        test_obj.backingchain_common_teardown()

        bkxml.sync()

        disk_obj.cleanup_disk_preparation(disk_type)
        if backing_file_type == 'block' and disk_type == 'file':
            disk_obj.cleanup_block_disk_preparation(vg0, lv1)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    disk_type = params.get('disk_type')
    target_disk = params.get('target_disk')
    backing_file_type = params.get('backing_file_type')
    backing_format = params.get('backing_format')
    disk_dict = eval(params.get('disk_dict', '{}'))
    commit_options = params.get('commit_options')
    expected_chain_index = params.get('expected_chain')
    top_index = params.get('top_image_suffix')
    base_index = params.get('base_image_suffix')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vg0, lv0, lv1 = 'vg0', 'lv0', 'lv1'

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
