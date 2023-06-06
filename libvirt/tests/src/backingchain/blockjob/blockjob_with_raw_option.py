import os
import time

from virttest import virsh
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Test blockjob with --raw option

    """
    def setup_raw_list():
        """
        Prepare running domain and do blockcopy
        """
        test.log.info("TEST_SETUP1:Start vm and clean exist copy file")
        test_obj.backingchain_common_setup(remove_file=True,
                                           file_path=tmp_copy_path)

        test.log.info("TEST_SETUP2:Do blockcopy")
        cmd = "blockcopy %s %s %s --wait --verbose --transient-job " \
              "--bandwidth %s " % (vm_name, dev, tmp_copy_path, bandwidth)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)

    def test_raw_list():
        """
        Do blockjob with raw option.

        1) Do blockjob with raw option twice.
        2) Check cur value is changing.
        3) Execute with --pivot and check raw option return empty.
        """
        if not libvirt.check_blockjob(vm_name, dev, "progress", "100(.00)?"):
            _check_cur()
            _abort_job()
        else:
            test.error('Blockcopy finished too fast, unable to check raw result,\
             Please consider to reduce bandwith value to retest this case.')

    def teardown_raw_list():
        """
        After blockcopy, clean file
        """
        # If some steps are broken by accident, execute --abort to
        # stop blockcopy job
        virsh.blockjob(vm_name, dev, options=' --abort', debug=True,
                       ignore_status=True)

        test_obj.clean_file(tmp_copy_path)
        bkxml.sync()

    def _check_cur():
        """
        Do twice blockjob --raw after blockcopy, and check the value of cur is
        changed according to the progress of blockcopy.
        """
        test.log.info("TEST_STEP1: Do blockjob with --raw and check cur changed")
        res_1 = virsh.blockjob(vm_name, dev, options=test_option, debug=True,
                               ignore_status=False)
        cur1 = libvirt_misc.convert_to_dict(res_1.stdout_text.strip(),
                                            pattern=r'(\S+)=(\S+)')['cur']
        time.sleep(1)
        res_2 = virsh.blockjob(vm_name, dev, options=test_option, debug=True,
                               ignore_status=False)
        cur2 = libvirt_misc.convert_to_dict(res_2.stdout_text.strip(),
                                            pattern=r'(\S+)=(\S+)')['cur']
        test.log.info('cur1 is %s , cur2 is %s', cur1, cur2)

        if int(cur1) >= int(cur2):
            test.fail('Two times cur value is not changed according'
                      ' to the progress of blockcopy.')

    def _abort_job():
        """
        Abort the job and check if it's aborted successfully.
        """
        test.log.info("Check if the job can be aborted successfully")
        if utils_misc.wait_for(
                lambda: libvirt.check_blockjob(vm_name,
                                               dev, "progress", "100(.00)?"), 100):
            virsh.blockjob(vm_name, dev, options=' --pivot',
                           debug=True,
                           ignore_status=False)
        else:
            test.fail("Blockjob timeout in 100 sec.")

        test.log.info("TEST_STEP3: Check the output of 'blockjob --raw' "
                      "after aborting the job")
        result = virsh.blockjob(vm_name, dev, options=test_option,
                                debug=True,
                                ignore_status=False)
        if result.stdout_text.strip():
            test.fail("It should return empty after pivot, but get:%s",
                      result.stdout_text.strip())

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case_name = params.get('case_name', 'blockjob')
    test_option = params.get('option_value', '')
    bandwidth = params.get('bandwidth', '1000')
    dev = params.get('disk')
    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)
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
