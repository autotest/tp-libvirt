from avocado.utils import process

from virttest import utils_misc
from virttest import utils_libvirtd
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Do blockcommit/blockpull/blockcopy with snapshots after
    restart the vm/libvirtd.

    1) Prepare backing file.
    2) Create snap with backing file.
    3) Destroy guest/Reboot guest/Restart libvirtd or virtqemud.
    4) Do blockcommit/blockpull/blockcopy
    """

    def setup_test():
        """
        Prepare backing file, snap chain and lifecycle.
        """
        test.log.info('TEST_SETUP1: Prepare backing file and snap chain')
        test_obj.backingchain_common_setup()

        for file_name in backing_list:
            cmd = "cd %s && qemu-img create -f qcow2 -o backing_fmt=qcow2 -b " \
                  "%s %s" % (base_dir, file_name[0], file_name[1])
            process.run(cmd, shell=True)

        for file_name in backing_list:
            test_obj.prepare_snapshot(snap_path=base_dir+file_name[1], snap_num=1,
                                      snap_name="snap_%s" % file_name[1][-1],
                                      option=snap_option, extra=snap_extra)

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test_obj.snap_path_list = disk_obj.get_source_list(vmxml, disk_type,
                                                           target_disk)[::-1]
        test.log.debug("After create snapshots, the vmxml is:\n%s", vmxml)

        test.log.info('TEST_SETUP2: Prepare backingfile and snap chain')
        prepare_lifecycle()

    def test_blockcommit():
        """
        Do blockcommit
        """
        test.log.info("TEST_STEP1:Do blockcommit")
        virsh.blockcommit(vm.name, target_disk,
                          commit_option % (test_obj.snap_path_list[2],
                                           test_obj.snap_path_list[1]),
                          ignore_status=False, debug=True)

        test.log.info("TEST_STEP2:Check backingchain")
        check_backingchain(expected_chain_index)

    def test_blockpull():
        """
        Do blockpull
        """
        test.log.info("TEST_STEP1:Do blockpull")
        virsh.blockpull(vm.name, target_disk,
                        pull_option % (test_obj.snap_path_list[1]),
                        ignore_status=False, debug=True)

        test.log.info("TEST_STEP2:Check backingchain")
        check_backingchain(expected_chain_index)

    def test_blockcopy():
        """
        Do blockcopy
        """
        test.log.info("TEST_STEP1:Do blockcopy")
        virsh.blockcopy(vm_name, target_disk, test_obj.copy_image,
                        options=blockcopy_options, ignore_status=False,
                        debug=True)

        test.log.info("TEST_STEP2:Check backingchain")
        check_backingchain(expected_chain_index)

    def teardown_test():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()
        bkxml.sync()

        test_obj.clean_file(test_obj.copy_image)
        for file_name in backing_list:
            test_obj.clean_file(file_name[1])

    def prepare_lifecycle():
        """
        Destroy guest/Reboot guest/Restart libvirtd or virtqemud.
        """
        if lifecycle == "destroy_guest":
            virsh.destroy(vm_name, ignore_status=False, debug=True)
            virsh.start(vm_name, ignore_status=False, debug=True)
        elif lifecycle == "reboot_guest":
            virsh.reboot(vm_name, ignore_status=False, debug=True)
        elif lifecycle == "restart_service":
            libvirtd = utils_libvirtd.Libvirtd()
            libvirtd.restart()

        if not utils_misc.wait_for(lambda: vm.is_alive(), 20, first=2):
            test.error("Guest state should be active")
        vm.wait_for_login().close()

    def check_backingchain(expected_chain_index):
        """
        Check the current backingchain list is correct

        :params: expected_chain_index: expected chain snapshot index, eg "4>2>1"
        """

        expected_chain = test_obj.convert_expected_chain(expected_chain_index)
        check_obj.check_backingchain_from_vmxml(disk_type, target_disk,
                                                expected_chain)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    target_disk = params.get("target_disk")
    disk_type = params.get("disk_type")
    snap_option = params.get("snap_option")
    snap_extra = params.get("snap_extra")
    lifecycle = params.get("lifecycle")
    operation = params.get("operation")
    blockcopy_options = params.get('blockcopy_options')
    commit_option = params.get('commit_option')
    pull_option = params.get('pull_option')
    expected_chain_index = params.get('expected_chain')
    base_dir = params.get("base_dir")

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    test_obj.copy_image = base_dir+"tmp.img"
    first_disk_source = libvirt_disk.get_first_disk_source(vm)
    backing_list = eval(params.get('backing_list', '{}') % first_disk_source)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    run_test = eval("test_%s" % operation)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
