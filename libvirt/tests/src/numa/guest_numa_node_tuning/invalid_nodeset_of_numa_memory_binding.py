# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from virttest import libvirt_version
from virttest import virsh
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.numa import numa_base


def run(test, params, env):
    """
    Verify that error msg prompts when starting a guest vm with
    invalid nodeset of numa memory binding
    """

    def setup_test():
        """
        Prepare init xml
        """
        test.log.info("TEST_SETUP: Set hugepage and guest boot")
        numa_obj = numa_base.NumaTest(vm, params, test)
        numa_obj.check_numa_nodes_availability()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)

        result = virsh.define(vmxml.xml, debug=True, ignore_status=True)
        if libvirt_version.version_compare(9, 4, 0) and \
                tuning == "restrictive":
            libvirt.check_result(result, expected_fails=define_err,
                                 check_both_on_error=True)
            return
        else:
            libvirt.check_exit_status(result)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        test.log.debug("The init xml is:\n%s", vmxml)

    def run_test():
        """
        Start vm and check result
        """
        test.log.info("TEST_STEP1: Start vm and check result")
        try:
            vm.start()
            if vm.is_alive():
                test.fail("Guest state should not be running")
        except virt_vm.VMStartError as detail:
            if not re.search(error_msg, str(detail)):
                test.fail("Expect '%s' in '%s' " % (error_msg, str(detail)))
            else:
                test.log.debug("Got '%s' in '%s'" % (error_msg, detail))

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    vm_attrs = eval(params.get("vm_attrs"))
    tuning = params.get("tuning")
    error_msg = params.get("error_msg")
    define_err = params.get("define_err")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
