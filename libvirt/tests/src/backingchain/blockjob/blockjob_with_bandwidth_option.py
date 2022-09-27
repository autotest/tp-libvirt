import os
import re

from virttest import virsh
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base


def run(test, params, env):
    """
    Test blockjob with --bandwidth option

    """
    def setup_test():
        """
        Prepare running domain and do blockcopy
        """
        test.log.info("TEST_SETUP1:Start vm and clean exist copy file")
        test_obj.backingchain_common_setup(remove_file=True,
                                           file_path=tmp_copy_path)

        cmd = "blockcopy %s %s %s --wait --verbose --transient-job " \
              "--bandwidth %s " % (vm_name, target_disk, tmp_copy_path, bandwidth)
        virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                           auto_close=True)
        test.log.info("TEST_SETUP2:Do blockcopy with: %s", cmd)
        virsh_session.sendline(cmd)

    def run_test():
        """
        Do blockjob with different bandwidth option and check value
        """
        test.log.info("TEST_SETUP2:Do blockjob with bandwidth and check value")
        for i in range(1, update_times+1):
            option = params.get("option_%d" % i)
            bandwidth_value = get_bandwidth_value(option)

            ret = virsh.blockjob(vm_name, target_disk, option, debug=True)
            libvirt.check_exit_status(ret)

            if not utils_misc.wait_for(
                    lambda: libvirt.check_blockjob(vm_name, target_disk,
                                                   "bandwidth", bandwidth_value),
                    10, step=0.1):
                test.fail('Bandwidth should return: %s' % bandwidth_value)

    def teardown_test():
        """
        Abort after blockcopy and clean file
        """
        virsh.blockjob(vm_name, target_disk, options=' --abort', debug=True,
                       ignore_status=True)
        test_obj.clean_file(tmp_copy_path)
        bkxml.sync()

    def get_bandwidth_value(blockjob_option):
        """
        Get bandwidth value from blockjob option and convert to
        expected value

        :param blockjob_option: blockjob option with bandwidth value
        :return: expected bandwidth value
        """
        bandwidth_value = re.findall(r'\d+', blockjob_option)[0]

        if "--bytes" in blockjob_option:
            return bandwidth_value

        return str(int(bandwidth_value)*1024*1024)

    # Process cartesian parameters
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    update_times = int(params.get('update_times'))
    bandwidth = params.get('bandwidth', '1000')
    target_disk = params.get('target_disk')

    test_obj = blockcommand_base.BlockCommand(test, vm, params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    tmp_copy_path = os.path.join(os.path.dirname(
        libvirt_disk.get_first_disk_source(vm)), "%s_blockcopy.img" % vm_name)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
