import re

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Do blockcommit with bandwidth

    1) Prepare snap chain
    2) Do blockcommit:
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
        Do blockcommit and check bandwidth value
        """
        top_option, base_option = '', ''
        if top_index:
            top_option = "--top %s" % test_obj.snap_path_list[int(top_index)-1]
        if base_index:
            base_option = " --base %s" % test_obj.snap_path_list[int(base_index)-1]

        cmd = "blockcommit %s %s %s" % (vm.name, target_disk,
                                        top_option+base_option+commit_option)
        test.log.info("TEST_STEP: Do blockcommit by: %s", cmd)

        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)
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
        Check the blockcommit result
        """
        if status_error:
            if not utils_misc.wait_for(lambda: re.findall(error_msg,
                                       virsh_session.get_stripped_output()), 30):
                test.fail('blockcommit should be failed, but get :%s' %
                          (virsh_session.get_stripped_output()))
        else:
            if not utils_misc.wait_for(
                    lambda: libvirt.check_blockjob(
                        vm.name, target_disk, "bandwidth", bandwith_value), 10, step=0.2):
                test.fail('Bandwidth should return: %s' % bandwith_value)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    top_index = params.get('top_image_suffix')
    base_index = params.get('base_image_suffix')
    commit_option = params.get('commit_option', '')
    bandwith_value = params.get('bandwith_value')
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
