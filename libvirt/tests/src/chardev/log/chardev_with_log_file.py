# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import aexpect
import os

from virttest import utils_test

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Verify data send through chardev can be correctly logged into log file
    """

    def setup_test():
        """
        Prepare a vm with the *only* console device.
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.remove_all_device_by_type("console")
        vmxml.remove_all_device_by_type("serial")
        libvirt_vmxml.modify_vm_device(
            vmxml=vmxml, dev_type=chardev, dev_dict=device_dict)

    def run_test():
        """
        1) Connect to the device using suitable client
        2) Login to the guest from client, run some commands
        3) Check log file.
        """
        test.log.info("TEST_STEP1: Connect to the device")
        vm.start()
        vm.wait_for_login().close()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The vmxml after start vm is:\n %s", vmxml)

        test.log.info("TEST_STEP2: Check append value in guest.")
        access_session = aexpect.ShellSession(access_cmd)
        utils_test.libvirt.virsh_console_login(
            access_session, params.get('username'), params.get('password'),
            debug=True, timeout=20)

        access_session.cmd_output(check_cmd)
        test.log.debug("Sent cmd: %s", check_cmd)

        test.log.info("TEST_STEP3: Check log file")
        vm_session = vm.wait_for_login()
        kernel_version = vm_session.cmd_output(check_cmd)
        test.log.debug("Get expected kernel version '%s' " % kernel_version)
        vm_session.close()

        libvirt.check_logfile("%s\n%s" % (check_cmd, kernel_version), log_file)

    def teardown_test():
        """
        Clean data.
        """
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()

        if os.path.exists(log_file):
            os.remove(log_file)

    vm_name = params.get("main_vm")
    device_dict = eval(params.get('device_dict', '{}'))

    access_cmd = params.get('access_cmd', "virsh console %s --force" % vm_name)
    chardev = params.get('chardev')
    log_file = params.get('log_file')
    check_cmd = params.get('check_cmd')

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
