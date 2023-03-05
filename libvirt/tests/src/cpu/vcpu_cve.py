#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Dan Zheng <dzheng@redhat.com>
#

"""
Test cases about CPU CVE/security
"""
import re

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_misc import cmd_status_output


def setup_test(vm, params, test):
    """
    Set up for tests

    :param vm: vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    vmxml_cpu = vmxml.cpu
    vmxml_cpu.mode = params.get('cpu_mode', 'host-model')
    vmxml.cpu = vmxml_cpu
    vmxml.sync()
    test.log.debug("After setup, vm xml is:\n%s", vm_xml.VMXML.new_from_dumpxml(vm.name))


def test_guest_cpu_cve_status(vm, params, test):
    """
    Test case for checking guest cpu CVE status

    :param vm: vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    virsh_option = {'debug': True, 'ignore_status': False}
    search_file_list = eval(params.get('search_file_list', '[]'))
    check_cmd = params.get('check_cmd')
    search_str = params.get('search_str')

    virsh.start(vm.name, **virsh_option)
    vm_session = vm.wait_for_login()
    for one_file in search_file_list:
        check_cmd = check_cmd + one_file
        _, output = cmd_status_output(check_cmd,
                                      ignore_status=False,
                                      session=vm_session)
        if re.search(search_str, output):
            vm_session.close()
            test.fail("'%s' is unexpectedly found in the output of "
                      "command '%s' in guest. "
                      "Output:\n'%s'" % (search_str, check_cmd, output))
        else:
            test.log.debug("'%s' is not found in the output of "
                           "command '%s' in guest as "
                           "expected.", search_str, check_cmd)
    vm_session.close()


def teardown_test(vmxml, test):
    """
    Tear down for tests

    :param vmxml: vm xml instance
    :param test: test object
    """
    test.log.debug("Teardown the test")
    if vmxml:
        test.log.debug("Recover the vm xml")
        vmxml.sync()


def run(test, params, env):
    """
    Test vcpu CVE related
    """
    case = params.get("test_case")
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    run_test = eval("test_%s" % case)

    try:
        setup_test(vm, params, test)
        run_test(vm, params, test)
    finally:
        teardown_test(bkxml, test)
