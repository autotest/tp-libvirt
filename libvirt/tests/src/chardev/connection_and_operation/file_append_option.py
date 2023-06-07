# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os
import platform
import stat

from avocado.utils import process

from virttest import virsh
from virttest import utils_logfile

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from virttest import utils_misc


def run(test, params, env):
    """
    Test the "append" attribute works correctly for
    chardevs

    Scenarios: append on ,append off, append default(off)
    """

    def setup_test():
        """
        Guest setup:
            Add below line in VM kernel command line: console=ttyS0,115200
        Host setups:
            file: create a empty file (in somewhere other than /root)
        """
        test.log.info("Setup env: Set guest kernel command line.")
        if not vm.set_kernel_console(device, speed,
                                     guest_arch_name=machine):
            test.fail("Config kernel for console failed.")

        test.log.info("Setup env: Create file on host")
        if os.path.exists(file_path):
            os.remove(file_path)
        process.run("touch %s " % file_path, shell=True)
        os.chmod(file_path, stat.S_IWUSR | stat.S_IEXEC)

        test.log.info("Setup env: Add device")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.remove_all_device_by_type('console')
        vmxml.remove_all_device_by_type('serial')
        libvirt_vmxml.modify_vm_device(
            vmxml=vmxml, dev_type=chardev, dev_dict=device_dict, index=dev_index)

    def run_test():
        """
        1) Start guest and check boot info, append value
        2) Check boot info for different append value
        """
        test.log.info("TEST_STEP1: Add device and start guest")
        vm.start()
        vm.wait_for_login().close()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The vmxml after start vm is:\n %s", vmxml)

        test.log.info("TEST_STEP2: Check append value in guest.")
        check_guest_append(append_value)

        test.log.info("TEST_STEP3: Check booting info")
        for item in boot_prompt:
            if not utils_misc.wait_for(lambda: utils_logfile.get_match_count(
                    file_path, item) == 1, 70):
                test.fail("Get %s should be once in %s" % (
                    item, file_path))

        test.log.info("TEST_STEP4: Destroy vm and start the vm")
        virsh.destroy(vm_name)
        virsh.start(vm_name)
        vm.wait_for_login().close()

        test.log.info("TEST_STEP5: Check boot info again")
        check_match_count(file_path, boot_prompt, append_value)

    def teardown_test():
        """
        Clean data.
        """
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()
        if os.path.exists(file_path):
            os.remove(file_path)

    def check_match_count(file_path, expected_str, append="off"):
        """
        Check match star count according to append value

        :params file_path: file path
        :params expected_str: expected string
        :params append: append value, default is off
        """
        expected_count = 2 if append == "on" else 1

        for item in expected_str:
            if not utils_misc.wait_for(
                    lambda: utils_logfile.get_match_count(
                        file_path, item) == expected_count, 70):
                test.fail("Get %s should be  %s times in %s" % (
                    item, expected_count, file_path))

    def check_guest_append(append):
        """
        Get attend xml pattern to check

        :params append : append value
        """
        if append == "default":
            pattern = [{'element_attrs': [".//source[@path='%s']" % file_path]}]
        else:
            pattern = [{'element_attrs': [".//source[@path='%s']" % file_path]},
                       {'element_attrs': [".//source[@append='%s']" % append]}]
        test.log.debug('Checking pattern is %s', pattern)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, pattern)

    vm_name = params.get("main_vm")
    machine = platform.machine()

    device = params.get('device')
    speed = params.get('speed')
    chardev = params.get('chardev')
    dev_index = int(params.get('dev_index'))
    append_value = params.get('append_value')
    file_path = params.get('file_path')

    device_dict = eval(params.get('device_dict', '{}'))
    boot_prompt = eval(params.get('boot_prompt'))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
