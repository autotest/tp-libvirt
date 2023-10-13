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
from virttest import utils_misc
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
    def get_nodeset_value():
        """
        Get nodeset value and format according cfg

        :return nodeset value
        """
        set_value = ''
        node_list = utils_misc.NumaInfo().online_nodes
        if node_set == "partially_inexistent":
            set_value = "%s-%s" % (node_list[-1], str(node_list[-1] + 1))
        elif node_set == "totally_inexistent":
            set_value = "%s-%s" % (str(node_list[-1] + 1), str(node_list[-1] + 2))

        params.update({'node_list': node_list})
        params.update({'set_value': set_value})

        return set_value

    def setup_test():
        """
        Prepare init xml
        """
        test.log.info("TEST_SETUP: Set hugepage and guest boot")
        numa_obj = numa_base.NumaTest(vm, params, test)
        numa_obj.check_numa_nodes_availability()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        set_value = get_nodeset_value()
        vmxml.setup_attrs(**eval(vm_attrs % set_value))

        result = virsh.define(vmxml.xml, debug=True, ignore_status=True)
        if libvirt_version.version_compare(9, 4, 0) and \
                tuning == "restrictive" and binding == "guest":
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
        error = error_msg % (str(params['node_list'][-1] + 1))
        error_1 = error_msg_1 % (str(params['set_value']))

        try:
            vm.start()
        except virt_vm.VMStartError as detail:
            if (not libvirt_version.version_compare(9, 4, 0)) and \
                    tuning in ["strict", "restrictive"] and binding == "host":
                if not re.search(error_1, str(detail)):
                    test.fail("Expect '%s' in '%s' " % (error_1, str(detail)))
            else:
                if not re.search(error, str(detail)):
                    test.fail("Expect '%s' in '%s' " % (error, str(detail)))

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

    vm_attrs = params.get("vm_attrs")
    tuning = params.get("tuning")
    node_set = params.get("node_set")
    binding = params.get("binding")
    error_msg = params.get("error_msg")
    error_msg_1 = params.get("error_msg_1")
    define_err = params.get("define_err")
    node_set = params.get("node_set")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
