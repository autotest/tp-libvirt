import os

from avocado.utils import process

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Do concurrent blockcommit/blockpull/blockcopy with relative path:

    1) Prepare a guest with snapshots in relative path.
    2) Do concurrent blockcommit/blockpull/blockcopy
    3) Check result.
    """

    def setup_test():
        """
        Prepare disk and relative path.
        """
        test.log.info("TEST_SETUP:Prepare relative path")

        test_obj.new_image_path, _ = disk_obj.prepare_relative_path(
            disk_type)
        disk_obj.add_vm_disk(disk_type, disk_dict,
                             new_image_path=test_obj.new_image_path,
                             size="800M")

        test_obj.backingchain_common_setup(remove_file=True, file_path=copy_path)

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        test.log.debug('vmxml after starting is:%s', vmxml)
        test_obj.snap_path_list = disk_obj.get_source_list(vmxml, disk_type,
                                                           test_obj.new_dev)[1:]

    def test_during_blockcopy():
        """
        Do blockcommit and blockpull during blockcopy
        """
        test.log.info("TEST_STEP1: Do blockcommit/blockpull during blockcopy")
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        cmd = "blockcopy %s %s %s" % (vm_name, target_disk,
                                      blockcopy_options % copy_path)
        virsh_session.sendline(cmd)
        test.log.debug("Do blockcopy by: %s", cmd)

        ret = virsh.blockcommit(vm.name, target_disk, commit_option,
                                debug=True, virsh_opt=virsh_opt)
        libvirt.check_result(ret, expected_fails=error_msg,
                             check_both_on_error=True)

        ret = virsh.blockpull(vm.name, target_disk, pull_option,
                              debug=True, virsh_opt=virsh_opt)
        libvirt.check_result(ret, expected_fails=error_msg,
                             check_both_on_error=True)

    def test_during_blockcommit():
        """
        Do blockcommit and blockpull during blockcommit
        """

        test.log.info("TEST_STEP1: Do blockcommit/blockpull during blockcommit")
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        cmd = " blockcommit %s %s %s" % (vm_name, target_disk,
                                         blockcommit_options)
        virsh_session.sendline(cmd)
        test.log.debug("Do blockcommit by: %s", cmd)

        ret = virsh.blockcommit(vm.name, target_disk,
                                commit_option % (test_obj.snap_path_list[0],
                                                 test_obj.snap_path_list[2]),
                                debug=True, virsh_opt=virsh_opt)

        libvirt.check_result(ret, expected_fails=error_msg,
                             check_both_on_error=True)

        ret = virsh.blockpull(vm.name, target_disk, pull_option,
                              debug=True, virsh_opt=virsh_opt)
        libvirt.check_result(ret, expected_fails=error_msg,
                             check_both_on_error=True)

    def teardown_test():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        test_obj.clean_file(copy_path)

        for folder in [chr(letter) for letter in
                       range(ord('a'), ord('a') + 4)]:
            rm_cmd = "rm -rf %s" % os.path.join(disk_obj.base_dir, folder)
            process.run(rm_cmd, shell=True)

        if disk_type == 'block':
            pvt = libvirt.PoolVolumeTest(test, params)
            pvt.cleanup_pool(**params)
            process.run("rm -rf %s" % pool_target)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case = params.get("case")
    target_disk = params.get("target_disk")
    disk_type = params.get("disk_type")
    disk_dict = eval(params.get('disk_dict', '{}'))

    virsh_opt = params.get("virsh_opt")
    blockcopy_options = params.get("blockcopy_options")
    blockcommit_options = params.get("blockcommit_options")

    commit_option = params.get("commit_option")
    pull_option = params.get("pull_option")
    error_msg = params.get("error_msg")
    pool_target = params.get("pool_target")

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    copy_path = os.path.join(disk_obj.base_dir, "copy.img")

    run_test = eval("test_%s" % case)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
