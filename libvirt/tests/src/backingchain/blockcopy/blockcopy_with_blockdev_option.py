from avocado.utils import lv_utils
from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test blockcopy with blockdev option.

    1) Prepare an running guest.
    2) Create snap.
    3) Do blockcopy with --blockdev shallow and no shallow
    4) Check expected chain and disk hash
    """

    def setup_test():
        """
        Prepare active domain and snap chain
        """
        test.log.info("TEST_SETUP: Add disk and snap chain.")
        test_obj.new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        test_obj.lvm_list = _pre_lvm()
        _do_snap(test_obj.lvm_list)

    def run_test():
        """
        Test blockcopy with --blockdev option.
        """
        session = vm.wait_for_login()
        expected_hash = test_obj.get_hash_value(session,
                                                check_item="/dev/"+test_obj.new_dev)

        test.log.info("TEST_STEP1: Do blockcopy.")
        virsh.blockcopy(vm_name, device, blockcopy_options % test_obj.lvm_list[1],
                        ignore_status=False,
                        debug=True)

        test.log.info("TEST_STEP2: Check backingchain and hash value.")
        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, device, expected_chain)

        check_obj.check_hash_list(["/dev/"+test_obj.new_dev], [expected_hash],
                                  session)
        session.close()

    def teardown_test():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()

        bkxml.sync()
        disk_obj.cleanup_block_disk_preparation(disk_obj.vg_name, lv_name+"1")

        process.run("rm -rf %s" % ("/dev/%s" % disk_obj.vg_name))
        process.run("rm -f %s" % file_snap_path)

    def _do_snap(lvm_list):
        """
        Create snap chain

        :params lvm_list: lvm list
        """
        snap_path = file_snap_path
        if disk_type == "block" or disk_type == "rbd_with_auth":
            test_obj.tmp_dir = "/dev/%s/%s" % (vg_name, lv_name)
            snap_path = lvm_list[0]

        test_obj.prepare_snapshot(snap_num=snap_nums, snap_path=snap_path,
                                  option=snap_option, extra=extra_option,
                                  clean_snap_file=False)
        test_obj.snap_path_list = test_obj.lvm_list

    def _pre_lvm():
        """
        Create the left lvm as snap path, the first lvm is created by add_vm_disk code
        """
        if disk_type != "block":
            # for block disk, volume group has been created in add_vm_disk()
            # Need to create volume group for file/rbd disk
            device_name = libvirt.setup_or_cleanup_iscsi(is_setup=True)
            lv_utils.vg_create(vg_name, device_name)

        path = []
        for i in range(1, lvm_num):
            source_path = '/dev/%s/%s' % (disk_obj.vg_name, lv_name + str(i))
            lv_utils.lv_create(disk_obj.vg_name, lv_name + str(i), vol_size)
            path.append(source_path)
        return path

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    device = params.get('target_disk')
    disk_type = params.get('disk_type', '')
    disk_dict = eval(params.get('disk_dict', '{}'))

    lvm_num = int(params.get('lvm_num'))
    snap_nums = int(params.get("snap_nums"))
    file_snap_path = params.get("file_snap_path")
    blockcopy_options = params.get('blockcopy_option')
    snap_option = params.get('create_snap_option', '')
    extra_option = params.get('snap_extra', '')
    expected_chain_index = params.get('expected_chain')
    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    # Get vm xml
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)
    vg_name, lv_name, vol_size = 'vg0', 'lv', '300M'

    try:
        # Execute test
        setup_test()
        run_test()

    finally:
        teardown_test()
        bkxml.sync()
