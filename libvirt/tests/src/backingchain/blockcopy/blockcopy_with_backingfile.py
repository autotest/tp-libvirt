import os

from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test blockcopy with different options.

    1) Prepare an running guest.
    2) Create snap.
    3) Do blockcopy.
    4) Check status by 'qemu-img info'.
    """

    def setup_blockcopy_shallow_and_reuse_external():
        """
        Prepare expected disk type, snapshots, blockcopy path.
        """
        test.log.info("TEST_SETUP: Start setup.")
        test_obj.new_image_path = disk_obj.add_vm_disk(
            disk_type, disk_dict, size="500M")

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        snap_path = ""
        if disk_type == "block":
            snap_path = libvirt.create_local_disk(
                "lvm", size="100M", vgname=disk_obj.vg_name, lvname=lv1)
            cmd = "qemu-img create -f qcow2 %s 100M" % snap_path
            process.run(cmd, shell=True, verbose=True)

        test.log.info("TEST_STEP1: Prepare snap chain :%s", test_obj.snap_path_list)
        test_obj.prepare_snapshot(snap_num=1, snap_path=snap_path,
                                  option=create_snap_option,
                                  extra=snap_extra)
        _prepare_dest_blockcopy_path()

    def test_blockcopy_shallow_and_reuse_external():
        """
        Do blockcopy and abort job ,than check hash and vmxml chain
        """
        session = vm.wait_for_login()
        status, _ = session.cmd_status_output("which sha256sum")
        if status:
            test.error("Not find sha256sum command on guest.")
        ret, expected_hash = session.cmd_status_output("sha256sum %s" %
                                                       "/dev/" + test_obj.new_dev)

        test.log.info("TEST_STEP3:Do blockcopy")
        virsh.blockcopy(vm_name, device, tmp_copy_path,
                        options=blockcopy_options, ignore_status=False,
                        debug=True)
        test.log.info("TEST_STEP4:Execute%s after blockcopy", execute_option)
        virsh.blockjob(vm_name, device, execute_option, ignore_status=False,
                       debug=True)

        expected_chain = ''
        if execute_option == "--abort":
            expected_chain = [test_obj.snap_path_list[0], test_obj.new_image_path]
        elif execute_option == "--pivot":
            expected_chain = [tmp_copy_path, test_obj.copy_image]
        check_obj.check_backingchain_from_vmxml(disk_type, device, expected_chain)
        check_obj.check_hash_list(["/dev/" + test_obj.new_dev], [expected_hash],
                                  session)
        session.close()

    def teardown_blockcopy_shallow_and_reuse_external():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()
        bkxml.sync()

        if os.path.exists(tmp_copy_path):
            process.run('rm -f %s' % tmp_copy_path)
        if os.path.exists(test_obj.new_image_path):
            process.run('rm -f %s' % test_obj.new_image_path)
        disk_obj.cleanup_disk_preparation(disk_type)

    def _prepare_dest_blockcopy_path():
        """
        Prepare a destination image which has backing file to do blockcopy
        """
        if disk_type == 'file':
            test_obj.copy_image = data_dir.get_data_dir() + \
                                  '/copy_dst_base_image.qcow2'
            libvirt.create_local_disk('file', test_obj.copy_image,
                                      size='500M', disk_format="qcow2")
        elif disk_type == 'block':
            test_obj.copy_image = libvirt.create_local_disk(
                "lvm", size="100M", vgname=disk_obj.vg_name, lvname=lv3)

        create_backing_cmd = "qemu-img create -f qcow2 -F %s -b %s %s" % (
            backing_fmt, test_obj.copy_image, tmp_copy_path)
        process.run(create_backing_cmd, shell=True)
        test.log.info("TEST_STEP2: Prepared backingfile :%s>%s", test_obj.copy_image, tmp_copy_path)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case_name = params.get('case_name', '')
    device = params.get('target_disk')
    blockcopy_options = params.get('blockcopy_option')
    disk_type = params.get("disk_type")
    disk_dict = eval(params.get('disk_dict', '{}'))
    execute_option = params.get("execute_option")
    create_snap_option = params.get("create_snap_option")
    snap_extra = params.get("snap_extra", "")
    backing_fmt = params.get("backing_fmt", "qcow2")
    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    # Get vm xml
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)
    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)
    disk_obj.vg_name, disk_obj.lv_name, lv1, lv3 = 'vg0', 'lv0', "lv1", "lv2"
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
