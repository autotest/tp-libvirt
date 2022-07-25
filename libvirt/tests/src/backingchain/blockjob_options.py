import logging
import os
import time

LOG = logging.getLogger('avocado.' + __name__)

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_misc
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions


def run(test, params, env):
    """
    Test blockjob operation

    """
    def setup_blockjob_raw():
        """
        Prepare running domain and do blockcopy
        """
        if not vm.is_alive():
            vm.start()

        if os.path.exists(tmp_copy_path):
            process.run('rm -rf %s' % tmp_copy_path)

        cmd = "blockcopy %s %s %s --wait --verbose --transient-job " \
              "--bandwidth %s " % (vm_name, dev, tmp_copy_path, bandwidth)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)

    def test_blockjob_raw():
        """
        Do blockjob with raw option.

        1) Prepare a running guest.
        2) Do blockcopy.
        3) Do blockjob with raw option twice and check cur value is changing
        4) Execute with --pivot and check raw option return empty
        """
        # Confirm blockcopy not finished.
        if not libvirt.check_blockjob(vm_name, dev, "progress", "100"):
            res_1 = virsh.blockjob(vm_name, dev, options=test_option, debug=True,
                                   ignore_status=False)
            cur1 = libvirt_misc.convert_to_dict(res_1.stdout_text.strip(),
                                                pattern=r'(\S+)=(\S+)')['cur']
            time.sleep(1)
            res_2 = virsh.blockjob(vm_name, dev, options=test_option, debug=True,
                                   ignore_status=False)
            cur2 = libvirt_misc.convert_to_dict(res_2.stdout_text.strip(),
                                                pattern=r'(\S+)=(\S+)')['cur']
            LOG.debug('cur1 is %s , cur2 is %s', cur1, cur2)

            if int(cur1) >= int(cur2):
                test.fail('Two times cur value is not changed according'
                          ' to the progress of blockcopy.')

            # Abort job and check abort success.
            if utils_misc.wait_for(
                    lambda: libvirt.check_blockjob(vm_name,
                                                   dev, "progress", "100"), 100):
                virsh.blockjob(vm_name, dev, options=' --pivot',
                               debug=True,
                               ignore_status=False)
            else:
                test.fail("Blockjob timeout in 100 sec.")
            # Check no output after abort.
            result = virsh.blockjob(vm_name, dev, options=test_option,
                                    debug=True,
                                    ignore_status=False)
            if result.stdout_text.strip():
                test.fail("Not return empty after pivot, but get:%s",
                          result.stdout_text.strip())
        else:
            test.error('Blockcopy finished too fast, unable to check raw result,\
             Please consider to reduce bandwith value to retest this case.')

    def teardown_blockjob_raw():
        """
        After blockcopy, clean file
        """
        # If some steps are broken by accident, execute --abort to
        # stop blockcopy job
        virsh.blockjob(vm_name, dev, options=' --abort', debug=True,
                       ignore_status=True)
        # Clean copy file
        process.run('rm -rf %s' % tmp_copy_path)

    def setup_blockjob_async():
        setup_blockjob_raw()

    def test_blockjob_async():
        """
        Check --async options which implies --abort;
        request but don't wait for job end

        1) Execute async option after blockcopy operation.
        2) Check BLOCK_JOB_CANCELLED event in qemu-monitor-event.
        3) Check blockjob --info get no block job.
        """
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(event_cmd % vm_name)
        virsh.blockjob(vm_name, dev, options=test_option, debug=True,
                       ignore_status=False)

        if not utils_misc.wait_for(
                lambda: expected_event in virsh_session.get_stripped_output(),
                10, step=0.01):
            test.fail('Not find %s in event output:%s' % (
                expected_event, virsh_session.get_stripped_output()))

        def _check_blockjob_info():
            """
            Check whether blockjob is successfully aborted.

            :return: True if blockjob is aborted, False if not
            """
            ret = virsh.blockjob(vm_name, dev, "--info", debug=True)
            return "No current block job" in ret.stdout_text.strip()

        if not utils_misc.wait_for(_check_blockjob_info, 2, step=0.1):
            test.fail('After executing blockjob with --async,'
                      ' blockjob still working')

    def teardown_blockjob_async():
        """
        After blockcopy, clean file
        """
        # Clean copy file
        process.run('rm -rf %s' % tmp_copy_path)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    case_name = params.get('case_name', 'blockjob')
    test_option = params.get('option_value', '')
    bandwidth = params.get('bandwidth', '1000')
    dev = params.get('disk')
    event_cmd = params.get("event_cmd")
    expected_event = params.get('expected_event')
    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    check_obj = check_functions.Checkfunction(test, vm, params)

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
        bkxml.sync()
