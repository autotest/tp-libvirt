# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import aexpect
import re

from virttest import virsh
from virttest import utils_test

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Test virsh console client can release console and return to host
     when guest shutdown.
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
        2) Connect to vm .
        3) Shutdown and console is closed.
        """
        test.log.info("TEST_STEP1: Add chardev and start guest")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.modify_vm_device(
            vmxml=vmxml, dev_type=chardev, dev_dict=device_dict)
        vm.start()
        vm.wait_for_login().close()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The vmxml after start vm is:\n %s", vmxml)

        test.log.info("TEST_STEP2: Connect to vm and check shutdown works.")
        if dev == "console":
            # Check with 'virsh console vm_name'
            access_session = aexpect.ShellSession(access_cmd % vm_name)
            utils_test.libvirt.virsh_console_login(
                access_session, params.get('username'), params.get('password'),
                debug=True)
            try:
                access_session.cmd_output(release_cmd)
                test.log.debug("Sent cmd: '%s' in console" % release_cmd)
            except (aexpect.ShellError, aexpect.ExpectError) as detail:
                if expected_msg not in str(detail):
                    test.fail('Expect shell terminated, but found %s' % detail)
                else:
                    test.log.debug("Got '%s' in '%s' " % (expected_msg, detail))

        elif dev == "serial":
            # Check with 'virsh' and 'console vm_name'
            access_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC)
            access_session.sendline(access_cmd % vm_name)
            utils_test.libvirt.virsh_console_login(
                access_session, params.get('username'), params.get('password'),
                debug=True)

            result = access_session.cmd_output('list')
            # dom_output = virsh.dom_list("--all", debug=True).stdout.strip()
            if not re.search(expected_msg % vm_name, result):
                test.fail('Expect "%s", but found "%s"' % (expected_msg % vm_name, result))
            else:
                test.log.debug("Got '%s' in '%s' " % (expected_msg % vm_name, result))

    def teardown_test():
        """
        Clean data.
        """
        if vm.is_alive():
            vm.destroy(gracefully=False)
        bkxml.sync()

    vm_name = params.get("main_vm")
    dev = params.get('dev')
    release_cmd = params.get('release_cmd')
    expected_msg = params.get('expected_msg')
    chardev = params.get('chardev')
    access_cmd = params.get('access_cmd')
    device_dict = eval(params.get('device_dict', '{}'))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
