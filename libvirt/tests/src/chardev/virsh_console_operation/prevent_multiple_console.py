# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

import aexpect

from virttest import utils_test

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Verify that multiple console connections are prevented and user can
     force disconnect the existing connection.
    """

    def setup_test():
        """
        Make sure remove all other serial/console devices before test.
        """
        test.log.info("Setup env: Remove console and serial")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.remove_all_device_by_type('console')
        vmxml.remove_all_device_by_type('serial')
        vmxml.sync()

    def run_test():
        """
        1) Start guest with chardev.
        2) Connect to console device.
        3) Connect to console device with another terminal.
        4) Connect to the chardev with --force parameter.
        """
        test.log.info("TEST_STEP1: Start vm with chardev")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.modify_vm_device(
            vmxml=vmxml, dev_type=chardev, dev_dict=device_dict)
        vm.start()
        vm.wait_for_login().close()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The vmxml after start vm is:\n %s", vmxml)

        test.log.info("TEST_STEP2: Connect to vm with virsh console vm_name")
        access_session = aexpect.ShellSession("sudo -s")
        status = utils_test.libvirt.verify_virsh_console(
            access_session, params.get('username'), params.get('password'),
            debug=True)
        if not status:
            test.fail("Expect virsh console success but got failed ")

        test.log.info("TEST_STEP3: Connect with duplicated console session")
        new_session = aexpect.ShellSession(access_cmd % vm_name)
        try:
            status = utils_test.libvirt.verify_virsh_console(
                new_session, params.get('username'), params.get('password'),
                debug=True)
            if status:
                test.fail("Expect virsh console failed with '%s', but success" % error_msg)
        except Exception as detail:
            if not re.search(error_msg, str(detail)):
                test.fail("Duplicated console session should get '%s' "
                          "but got:\n%s" % (error_msg, str(detail)))
        new_session.close()

        test.log.info("TEST_STEP4: Connect to the chardev with --force")
        new_session = aexpect.ShellSession(force_cmd % vm_name)
        force_status = utils_test.libvirt.verify_virsh_console(
            new_session, params.get('username'), params.get('password'),
            debug=True)
        if not force_status:
            test.fail("Expect force console session should succeed, "
                      "but failed.")
        new_session.close()
        access_session.close()

    def teardown_test():
        """
        Clean data.
        """
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()

    vm_name = params.get("main_vm")
    force_cmd = params.get('force_cmd')
    chardev = params.get('chardev')
    access_cmd = params.get('access_cmd')
    error_msg = params.get('error_msg')
    device_dict = eval(params.get('device_dict', '{}'))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
