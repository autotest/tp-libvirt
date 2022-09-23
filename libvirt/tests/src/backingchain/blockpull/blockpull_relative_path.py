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
    Do blockpull with relative path.

    1) Prepare different type disk and relative path
        disk types: file, block, network(rbd with auth)
    2) Set vm status
        running VM
    3) Do blockpull
        Do blockpull with --keep-relative continuously
    4) Check result
    """

    def setup_test():
        """
        Prepare specific type disk and relative path.
        """
        test.log.info("TEST_SETUP1: Prepare relative path and new disk.")
        test_obj.new_image_path, _ = disk_obj.prepare_relative_path(disk_type)

        disk_dict, kwargs = get_disk_param()
        disk_obj.add_vm_disk(disk_type, disk_dict,
                             new_image_path=test_obj.new_image_path, **kwargs)

        test.log.info("TEST_SETUP2: Start vm and get relative path.")
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        get_relative_path()

    def test_keep_relative():
        """
        Do blockpull and check backingchain result
        """
        test.log.info("TEST_STEP1: Do blockpull.")
        do_several_blockpull()

        test.log.info("TEST_STEP2: Check backingchain.")
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

    def get_relative_path():
        """
        Get relative path.
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test_obj.snap_path_list = disk_obj.get_source_list(vmxml, disk_type,
                                                           test_obj.new_dev)[1:]
        test.log.info("After start vm with relative path, "
                      "The xml is:\n%s", vmxml)

    def get_disk_param():
        """
        Get the param value according to different type disk.

        :return disk_dict and **kwargs
        """
        kwargs = {}
        if disk_type == "rbd_with_auth":
            kwargs.update({'no_update_dict': True})
            relative = test_obj.get_relative_path(test_obj.new_image_path)

            disk_dict = eval(params.get('disk_dict', '{}')
                             % (relative[0], relative[1], relative[2], relative[3]))
        else:
            disk_dict = eval(params.get("disk_dict", "{}"))

        return disk_dict, kwargs

    def do_several_blockpull():
        """
        Do several times blockpull
        """
        for i in range(0, pull_times):
            base_option = " --base %s" % test_obj.snap_path_list[i]
            virsh.blockpull(vm.name, test_obj.new_dev, base_option + pull_options,
                            ignore_status=False, debug=True,
                            virsh_opt=virsh_opt)

    vm_name = params.get("main_vm")
    disk_type = params.get('disk_type')
    pull_options = params.get('pull_options')
    expected_chain_index = params.get('expected_chain')
    virsh_opt = params.get('virsh_opt')
    pool_target = params.get('pool_target')

    test_scenario = params.get('test_scenario')
    pull_times = int(params.get('pull_times'))
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

    run_test = eval("test_%s" % test_scenario)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
