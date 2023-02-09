import re

from virttest import virsh, utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Do blockcopy with bandwidth option

    1) Do blockcopy with bandwidth by: bytes, mb, number, letter
    2) Check result
    """

    def setup_test():
        """
        Prepare active guest.
        """
        test.log.info("Setup env.")
        test_obj.backingchain_common_setup()

    def run_positive_test():
        """
        Do blockcopy with bandwidth value
        """
        test.log.info("TEST_STEP: Do blockcopy with bandwidth")
        cmd = "blockcopy %s %s %s" % (vm_name, target_disk, blockcopy_options)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        virsh_session.sendline(cmd)
        test.log.debug("Blockcopy cmd:%s" % cmd)

        check_blockjob_bandwidth()

    def run_negative_test():
        """
        Do blockcopy with invalid bandwidth value
        """
        test.log.info("TEST_STEP: Do blockcopy with bandwidth")
        result = virsh.blockcopy(vm.name, target_disk,
                                 blockcopy_options,
                                 debug=True)
        libvirt.check_result(result, expected_fails=err_msg)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        virsh.blockjob(vm_name, target_disk, "--abort", debug=True,
                       ignore_status=True)
        test_obj.clean_file(copy_image)
        bkxml.sync()

    def check_blockjob_bandwidth():
        """
        Check blockjob bandwidth.
        """
        bandwidth = re.findall(r'\d+', blockcopy_options)[0]

        if "--bytes" in blockcopy_options:
            bandwith_value = bandwidth
        else:
            bandwith_value = str(int(bandwidth)*1024*1024)

        if not utils_misc.wait_for(
                lambda: libvirt.check_blockjob(
                    vm.name, target_disk, "bandwidth", bandwith_value), 10):
            test.fail('Bandwidth should return: %s' % bandwith_value)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    target_disk = params.get('target_disk')
    test_scenario = params.get('test_scenario')
    blockcopy_options = params.get('blockcopy_options')
    copy_image = params.get('copy_image')
    err_msg = params.get('err_msg')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    run_test = eval('run_%s' % test_scenario)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
