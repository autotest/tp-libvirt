import os

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
    Do blockcommit with relative path.

    1) Prepare different type disk and relative path
        disk types: file, block, network(rbd with auth)
    2) Set vm status
        running VM
        paused VM(virsh suspend VM)
    3) Do blockcommit:
        Do active blockcommit with --keep-relative continuously
        Do inactive blockcommit with --keep-relative continuously
    4) Check result:
        hash value of file in vm are correct
    """

    def setup_test():
        """
        Prepare specific type disk and relative path.
        """
        test.log.info("TEST_SETUP1: Prepare relative path and new disk.")
        test_obj.new_image_path, _ = disk_obj.prepare_relative_path(disk_type)

        kwargs = {}
        if disk_type == "rbd_with_auth":
            kwargs.update({'no_secret': True})
        disk_obj.add_vm_disk(disk_type, disk_dict,
                             new_image_path=test_obj.new_image_path, **kwargs)

        test.log.info("TEST_SETUP2: Prepare vm state.")
        _pre_vm_state()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test_obj.snap_path_list = disk_obj.get_source_list(vmxml, disk_type,
                                                           test_obj.new_dev)[1:]

    def run_test():
        """
        Do blockcommit and check backingchain result , file hash value
        """
        if test_scenario == "active":
            test.log.info("TEST_STEP1: Do the first cycle blockcommit.")
            libvirt_disk.do_blockcommit_repeatedly(
                vm, test_obj.new_dev, commit_options, commit_times, virsh_opt=virsh_opt)

            expected_chain = test_obj.convert_expected_chain(expected_chain_index)
            check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                    expected_chain)

            test.log.info("TEST_STEP2: Create snap chain.")
            _do_snaps()

            test.log.info("TEST_STEP3: Do the second cycle blockcommit.")
            libvirt_disk.do_blockcommit_repeatedly(
                vm, test_obj.new_dev, commit_options, commit_times, virsh_opt=virsh_opt)

            expected_chain = test_obj.convert_expected_chain(expected_chain_index)
            check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                    expected_chain)

        elif test_scenario == "inactivate":
            test.log.info("TEST_STEP: Do blockcommit.")
            _do_blockcommit_inactive()
            expected_chain = test_obj.convert_expected_chain(expected_chain_index)[::-1]
            check_obj.check_backingchain_from_vmxml(disk_type, test_obj.new_dev,
                                                    expected_chain)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Recover test enviroment.")

        test_obj.backingchain_common_teardown()
        bkxml.sync()

        for folder in [chr(letter) for letter in
                       range(ord('a'), ord('a') + 4)]:
            rm_cmd = "rm -rf %s" % os.path.join(disk_obj.base_dir, folder)
            process.run(rm_cmd, shell=True)

        if disk_type == 'block':
            pvt = libvirt.PoolVolumeTest(test, params)
            pvt.cleanup_pool(**params)
            process.run("rm -rf %s" % pool_target)

        if disk_type == 'rbd_with_auth_disk':
            process.run("rm -f %s" % params.get("keyfile"))
            cmd = ("rbd -m {0} info {1} && rbd -m {0} rm {1}".format(
                mon_host, rbd_source_name))
            process.run(cmd, ignore_status=True, shell=True)

    def _pre_vm_state():
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        if vm_state == "paused":
            result = virsh.suspend(vm_name, ignore_status=False, debug=True)
            libvirt.check_exit_status(result)

    def _do_blockcommit_inactive():
        """
        Do several times inactive layer blockcommit
        """
        for i in range(0, commit_times):
            top, base = int(top_index.split(',')[i]), int(base_index.split(',')[i])
            top_option = " --top %s" % test_obj.snap_path_list[::-1][top]
            base_option = " --base %s" % test_obj.snap_path_list[::-1][base]

            virsh.blockcommit(vm.name, test_obj.new_dev,
                              top_option + base_option + commit_options,
                              ignore_status=False, debug=True, virsh_opt=virsh_opt)

    def _do_snaps():
        """
        Do snaps with different path
        """
        path_list = [test_obj.new_image_path] + test_obj.snap_path_list

        for i in range(snap_num):
            option = "%s %s --diskspec %s,file=%s%s" % (
                'snap%d' % i, snap_option, test_obj.new_dev, path_list[i], snap_extra)
            virsh.snapshot_create_as(vm.name, option, ignore_status=False, debug=True)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    disk_type = params.get('disk_type')
    disk_dict = eval(params.get('disk_dict', '{}'))
    commit_options = params.get('commit_options')
    expected_chain_index = params.get('expected_chain')
    top_index = params.get('top_image_suffix')
    base_index = params.get('base_image_suffix')
    virsh_opt = params.get('virsh_opt')
    pool_target = params.get('pool_target')

    test_scenario = params.get('test_scenario')
    vm_state = params.get('vm_state')
    commit_times = int(params.get('commit_times'))
    snap_option = params.get('snap_option', '')
    snap_extra = params.get('snap_extra', '')
    snap_num = int(params.get('snap_num', 0))
    mon_host = params.get('mon_host', '')
    rbd_source_name = params.get('image_path', '')

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
