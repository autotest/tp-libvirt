import re

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Do blockpull with bandwidth

    1) Prepare snap chain
    2) Do blockpull:
        bandwidth with mb.
        bandwidth with bytes.
        bandwidth with invalid values.
    3) Check result:
        Correct bandwidth value
    """
    def setup_test():
        """
        Prepare snapshots.
       """
        test.log.info("TEST_SETUP:Prepare snap chain .")
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        test_obj.prepare_snapshot(snap_num=snap_num)

    def run_test():
        """
        Do blockpull and check bandwidth value
        """
        base_option = ''
        if base_index:
            base_option = " --base %s" % test_obj.snap_path_list[int(base_index)-1]

        cmd = "blockpull %s %s %s" % (vm.name, target_disk, base_option+pull_option)
        test.log.info("TEST_STEP1: Do blockpull by: %s", cmd)

        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)
        test.log.info("TEST_STEP2: Check bandwidth value")
        _check_result(virsh_session)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        test_obj.backingchain_common_teardown()
        bkxml.sync()

    def _check_result(virsh_session):
        """
        Check the blockpull result
        """
        if status_error:
            if not utils_misc.wait_for(lambda: re.findall(error_msg,
                                       virsh_session.get_stripped_output()), 30):
                test.fail('blockpull should be failed, but get :%s' %
                          (virsh_session.get_stripped_output()))
        else:
            event_output = virsh_session.get_stripped_output()
            if "error:" in event_output:
                test.fail("Failed to do blockcopy with result:%s" % (event_output))
            if not utils_misc.wait_for(
                    lambda: libvirt.check_blockjob(
                        vm.name, target_disk, "bandwidth", bandwidth_value), 5, step=0.05):
                test.fail('Bandwidth should return: %s,  but get :%s' % (
                    bandwidth_value, virsh_session.get_stripped_output()))

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    base_index = params.get('base_image_suffix')
    pull_option = params.get('pull_option', '')
    bandwidth_value = params.get('bandwidth_value')
    error_msg = params.get('error_msg')
    snap_num = int(params.get('snap_num'))
    status_error = params.get("status_error", "no") == "yes"

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    # Create object
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    test_obj.original_disk_source = libvirt_disk.get_first_disk_source(vm)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
