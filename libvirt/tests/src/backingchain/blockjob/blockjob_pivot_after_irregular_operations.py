import os

from avocado.utils import process
from avocado.utils import linux_modules
from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions


def run(test, params, env):
    """
    Do blockjob --pivot after irregular operations

    S1: Do blockjob --pivot before block copy job is finished
    S2: Do blockjob --pivot after deleting the copy file
    """

    def setup_test():
        """
        Prepare active guest
        """
        test.log.info("TEST_SETUP: Setup env")
        test_obj.backingchain_common_setup()

    def setup_blk_dev(size):
        """
        Setup 2 block devices, one emulated and another via iSCSI server,
        both with the same size.

        :size: the disk size in gigabytes, like "3G"
        :return: A list containing the names of the two prepared disks.
        """
        test.log.debug("TEST_SETUP: Setting up 2 additional block devices...")
        cmd_lsblk = 'lsblk -d -o NAME -n'
        current_disk_names = process.run(cmd_lsblk, shell=True).stdout_text.strip().splitlines()
        # create 1st scsi disk
        libvirt.setup_or_cleanup_iscsi(True, image_size=size)
        # create 2nd scsi disk with same size
        size_in_mib = str(int(size.rstrip("G")) * 1024)
        libvirt.create_scsi_disk(scsi_option="", scsi_size=size_in_mib)
        updated_disk_names = process.run(cmd_lsblk, shell=True).stdout_text.strip().splitlines()
        new_disks = [disk for disk in updated_disk_names if disk not in current_disk_names]
        test.log.debug(f"Prepared 2 disks: {new_disks}")
        return new_disks

    def run_before_finish():
        """
        Do blockjob --pivot before block copy job is finished.
        """
        cmd = blockcopy_options % (vm_name, target_disk, tmp_copy_path)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)

        test.log.info("TEST_STEP1: Do blockcopy")
        virsh_session.sendline(cmd)
        test.log.debug("Blockcopy cmd:%s" % cmd)

        test.log.info("TEST_STEP2: Do blockjob with pivot")
        if not utils_misc.wait_for(
                lambda: libvirt.check_blockjob(vm_name, target_disk,
                                               "progress", "100(.00)?"), 2):

            result = virsh.blockjob(vm_name, target_disk,
                                    options=pivot_option, debug=True,
                                    ignore_status=True)
            libvirt.check_result(result, expected_fails=err_msg,
                                 check_both_on_error=True)
        else:
            test.error("Please reset bandwidth to test pivot "
                       "before blockcopy finished ")

    def run_delete_copy_file():
        """
        Do blockjob --pivot after deleting the destination copy file.
        """
        test.log.info("TEST_STEP1: Do blockcopy")
        virsh.blockcopy(vm_name, target_disk, tmp_copy_path,
                        blockcopy_options, ignore_status=False,
                        debug=True)
        check_obj.check_mirror_exist(vm, target_disk, tmp_copy_path)

        test.log.info("TEST_STEP2: Delete blockcopy file")
        test_obj.clean_file(tmp_copy_path)

        test.log.info("TEST_STEP3: Do blockjob with pivot")
        virsh.blockjob(vm_name, target_disk, options=pivot_option,
                       debug=True, ignore_status=False)

    def run_same_target():
        """
        Execute blockcopy operations with target block device being the same as or different from source.

        Steps:
            1. Prepare two distinct block devices on the host.
            2. Hotplug one disk to the VM with its source device set as one of the prepared block devices.
            3. Attempt negative test: perform blockcopy with target device identical to source.
            4. Perform positive test: execute blockcopy with target device being another prepared block device.

        :return: None
        """
        size = params.get("disk_size", "3G")
        blk_dev1, blk_dev2 = setup_blk_dev(size)

        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test.log.info("TEST_STEP1: Hotplug a disk with source device as one of the block devices on host")
        virsh.attach_disk(vm_name, f'/dev/{blk_dev1}', target_disk, debug=True)
        output1 = virsh.domblklist(vm_name).stdout_text
        test.log.info(f"After hotplugging, current VM disk list is: {output1}")

        test.log.info("TEST_STEP2: Perform blockcopy with identical source and target devices...")
        ret1 = virsh.blockcopy(vm_name, target_disk, f'/dev/{blk_dev1}', blockcopy_options)
        libvirt.check_result(ret1, expected_fails=err_msg)

        test.log.info("TEST_STEP3: Execute blockcopy with different source and target devices...")
        ret2 = virsh.blockcopy(vm_name, target_disk, f'/dev/{blk_dev2}', blockcopy_options)
        libvirt.check_result(ret2, expected_match='[100.00 %]')

    def teardown_test():
        """
        Clean data.
        """
        virsh.blockjob(vm_name, target_disk, options=abort_option,
                       debug=True, ignore_status=True)
        test_obj.clean_file(tmp_copy_path)
        bkxml.sync()
        if test_scenario == 'same_target':
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
            linux_modules.unload_module("scsi_debug")

    vm_name = params.get("main_vm")
    target_disk = params.get("target_disk")
    blockcopy_options = params.get("blockcopy_options")
    err_msg = params.get("err_msg")
    pivot_option = params.get("pivot_option")
    abort_option = params.get("abort_option")
    test_scenario = params.get("test_scenario")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)

    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)

    run_test = eval("run_%s" % test_scenario)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
