# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import aexpect

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Verify that:
    1) nodeset setting is ignored when auto memory placement is defined
    2) error prompts for mixing nodeset and auto memory placement settings
    """

    def setup_test():
        """
        Prepare init xml
        """
        test.log.info("TEST_SETUP: Define guest")
        vm_attrs = {'numa_memory': {'mode': tuning_mode,
                                    'placement': placement,
                                    'nodeset': nodeset}}

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()

    def run_test():
        """
        Start vm and check result
        """
        test.log.info("TEST_STEP1: Check xml")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        test.log.debug("Get related xml:\n%s", vmxml)
        libvirt_vmxml.check_guest_xml_by_xpaths(
            vmxml, eval(expected_xpaths % tuning_mode))

        test.log.info("TEST_STEP2: Edit gust and turn off validation")
        session = aexpect.ShellSession("sudo -s")
        session.sendline("virsh edit %s" % vm_name)
        test.log.debug("virsh edit cmd is:'%s'", edit_cmd)
        session.sendline(edit_cmd)
        session.send('\x1b')
        session.send('ZZ')
        _, text = session.read_until_any_line_matches(
            [r"%s" % error_msg], timeout=10, internal_timeout=1)
        test.log.debug("Checked '%s' exists in '%s'", (error_msg, text))
        test.log.debug("Input 'i' to turn off validation")
        session.sendline('i')

        test.log.info("TEST_STEP3: Check xml again")
        _, text = session.read_until_any_line_matches(
            [r"%s" % (success_msg % vm_name)], timeout=10, internal_timeout=1)
        session.close()
        test.log.debug("Checked '%s' exists in '%s'", (success_msg % vm_name,
                                                       text))

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(
            vmxml, eval(expected_xpaths % tuning_mode))

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    error_msg = params.get("error_msg")
    success_msg = params.get("success_msg")
    tuning_mode = params.get("tuning_mode")
    placement = params.get("placement")
    nodeset = params.get("nodeset")
    expected_xpaths = params.get("expected_xpaths", '{}')
    default_cmd = r":%s/memory mode='{}'/memory mode='{}' nodeset='{}'".format(
        tuning_mode, tuning_mode, nodeset)
    edit_cmd = params.get("edit_cmd", default_cmd)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
