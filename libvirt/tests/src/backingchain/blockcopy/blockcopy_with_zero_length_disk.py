import os

from virttest import data_dir
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    This case is to verify transient guest and zero-length disk
    for blockcopy.
    """

    def do_blockcopy(path):
        """
        Do blockcopy and check result

        :params path: blockcopy path
        """

        result = virsh.blockcopy(
            vm_name, target_disk, path, options=blockcopy_option,
            ignore_status=False, debug=True).stdout_text.strip()
        if blockcopy_msg not in result:
            test.fail("Expect to get '%s' in output '%s'" % (blockcopy_msg, result))

    def do_blockjob(option, expected_msg=''):
        """
        Do blockjob and check expected msg

        :params option: blockjob options
        :params expected_msg: expected msg in blockjob result.
        """
        result = virsh.blockjob(vm_name, target_disk, option,
                                debug=True).stderr_text.strip()
        if expected_msg:
            if expected_msg not in result:
                test.fail("Expect to get '%s' in '%s'" % (done_job_msg, result))

    def prepare_transient_guest():
        """
        Prepare transient guest.
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        dest_obj, _ = disk_obj.prepare_disk_obj(disk_type, disk_dict,
                                                size=disk_size)
        vmxml.devices = vmxml.devices.append(dest_obj)
        test.log.debug("Define guest by xml:\n%s", vmxml)
        virsh.undefine(vm_name, options='--nvram', debug=True,
                       ignore_status=False)
        virsh.create(vmxml.xml, shell=True)

    def run_test():
        """
        Do blockcopy with empty disk.
        Abort job with --abort and --pivot.
        Check domain xml config.
        """
        test.log.info("TEST_STEP1: Prepare a running transient guest.")
        prepare_transient_guest()

        test.log.info("TEST_STEP2: Do blockcopy for new disk")
        do_blockcopy(copy_path)

        test.log.info("TEST_STEP3: Check job status by blockjob.")
        libvirt.check_blockjob(vm_name, target_disk, "progress", "100(.00)?")

        test.log.info("TEST_STEP4: Abort the job with --abort.")
        do_blockjob("--abort")
        libvirt.check_blockjob(vm_name, target_disk, "none")

        test.log.info("TEST_STEP5: Do blockcopy again and pivot the job.")
        do_blockcopy(copy_path_1)
        libvirt.check_blockjob(vm_name, target_disk, "progress", "100(.00)?")
        do_blockjob("--pivot")

        test.log.info("TEST_STEP6: Check disk source.")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        new_disk_source = disk_obj.get_source_list(
            vmxml, disk_type, target_disk)[0]
        if new_disk_source != copy_path_1:
            test.fail("Expect to get '%s', but got '%s' in:\n'%s'" % (
                copy_path_1, new_disk_source, vmxml))

    def teardown_test():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        virsh.destroy(vm_name, debug=True)
        virsh.define(bkxml.xml, debug=True)

        if os.path.exists(copy_path):
            test_obj.clean_file(copy_path)
        if os.path.exists(copy_path_1):
            test_obj.clean_file(copy_path_1)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    target_disk = params.get("target_disk")
    disk_type = params.get('disk_type', '')
    disk_size = params.get('disk_size')
    disk_dict = eval(params.get('disk_dict', '{}'))
    blockcopy_option = params.get("blockcopy_option")
    blockcopy_msg = params.get("blockcopy_msg")
    done_job_msg = params.get("done_job_msg")

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    copy_path = data_dir.get_data_dir() + '/copy.qcow2'
    copy_path_1 = data_dir.get_data_dir() + '/copy_1.qcow2'

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        run_test()

    finally:
        teardown_test()
