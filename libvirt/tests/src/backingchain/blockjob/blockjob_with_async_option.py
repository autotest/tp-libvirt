import logging

from virttest import data_dir
from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Do blockjob with async option

    1) Prepare a running guest
    2) Do blockcopy with --async
    3) Check result
    """

    def setup_test():
        """
        Prepare a running guest
        """
        test.log.info("Setup env: prepare a running guest.")
        test_obj.copy_image = data_dir.get_data_dir() + '/rhel.copy'
        test_obj.backingchain_common_setup(remove_file=True,
                                           file_path=test_obj.copy_image)

    def run_test():
        """
        Do blockjob with --async option
        Check result
        """
        blkcopy_test()
        blkjob_async_test()
        check_blkjob_info()

    def teardown_test():
        """
        Clean up the test environment
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        # Clean up the copy image
        test_obj.clean_file(test_obj.copy_image)
        bkxml.sync()

    def blkcopy_test():
        """
        Do blockcopy to create a job before blockjob async test
        """
        test.log.info("TEST_STEP1: Do blockcopy")
        cmd = "blockcopy %s %s %s %s" % (vm_name, target_disk, test_obj.copy_image,
                                         blkcopy_options)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)

    def blkjob_async_test():
        """
        Check --async options which implies --abort

        1) Execute async option after blockcopy operation.
        2) Check BLOCK_JOB_CANCELLED event in qemu-monitor-event.
        3) Check blockjob --info get no block job.
        """
        test.log.info("TEST_STEP2: Do qemu-monitor-event")
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(event_cmd % vm_name)
        test.log.info("TEST_STEP3: Do blockjob with --async option")
        virsh.blockjob(vm_name, target_disk, options=blkjob_options, debug=True,
                       ignore_statusd=False)
        test.log.info("TEST_STEP4: Check the events")
        if not utils_misc.wait_for(
                lambda: expected_event in virsh_session.get_stripped_output(),
                10, step=0.01):
            test.fail('Not find %s in event output:%s' % (
                expected_event, virsh_session.get_stripped_output()))
        else:
            logging.debug("Can find %s in event oputput:%s" % (
                expected_event, virsh_session.get_stripped_output()))

    def check_blkjob_info():
        """
        Check the status of blockjob info
        """
        ret = virsh.blockjob(vm_name, target_disk, "--info", debug=True)
        if "No current block job" not in ret.stdout_text.strip():
            test.fail('After executing blockjob with --async,'
                      ' blockjob is still working')
        else:
            logging.debug("The blockcopy job is aborted as expected")

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    expected_log = params.get("expected_log")
    blkcopy_options = params.get("blockcopy_options")
    blkjob_options = params.get("blockjob_options")
    event_cmd = params.get("event_cmd")
    expected_event = params.get("expected_event")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)

    try:
        setup_test()
        run_test()
    finally:
        teardown_test()
