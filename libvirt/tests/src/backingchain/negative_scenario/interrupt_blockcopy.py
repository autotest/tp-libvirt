from virttest import data_dir
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Interrupt blockcopy on persistent domains with --transient-job,
    check the job doesn't recover and inactive xml doesn't be changed.
    """

    def run_test():
        """
        Do block operations and interrupt it before finish.
        Check disk not change
        """
        test.log.info("TEST_STEP1: Start the VM")
        virsh.start(vm_name)
        original_disk_source = libvirt_disk.get_first_disk_source(vm)

        test.log.info("TEST_STEP2: Do blockcopy and interrupt it before finish")
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        cmd = "blockcopy %s %s %s" % (vm_name, target_disk, target_disk)
        virsh_session.sendline(cmd)
        virsh_session.send_ctrl("^C")
        test.log.debug('Get blockcopy output:%s', (virsh_session.get_stripped_output()))

        ret = virsh.blockjob(vm_name, target_disk, "--info", debug=True)
        if "No current block job" not in ret.stdout_text.strip():
            test.fail('After executing blockjob with --async,'
                      ' blockjob is still working')

        test.log.info("TEST_STEP3: Check disk not change")
        new_source = libvirt_disk.get_first_disk_source(vm)
        if original_disk_source != new_source:
            test.fail("Disk source should not change to %s" % new_source)

    def teardown_test():
        """
        Clean env
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        test_obj.clean_file(copy_path)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    target_disk = params.get("target_disk")

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    copy_path = data_dir.get_data_dir() + '/copy.qcow2'

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        run_test()

    finally:
        teardown_test()
