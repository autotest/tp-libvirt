# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Verify memory allocation settings could be modified.

    Scenario:
    1:without numa
    2:with numa
    """
    def setup_test():
        """
        Prepare memory device
        """
        test.log.info("TEST_SETUP: Define vm.")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("After define vm, get vmxml is:\n%s", vmxml)

    def run_test():
        """
        1.Increase currentMemory value same with memory by virsh edit
        2.Increase currentMemory value bigger than memory by virsh edit
        3.Increase memory value to maxMemory by cmd virsh edit
        4.Increase memory value to maxMemory by cmd virus setmaxmem
        5.Start the guest then change memory to maxMemory by virsh setmaxmem
        """
        test.log.info("TEST_STEP1: Increase currentMemory value same with "
                      "memory by virsh edit")
        edit_cmd = r":%s/{0}/{1}".format(current_mem_xml % current_mem,
                                         current_mem_xml % mem_value)
        status = libvirt.exec_virsh_edit(vm_name, [edit_cmd])
        if not status:
            test.fail('Edit guest failed')
        check_mem_config(eval(current_mem_xpath % mem_value))
        test.log.info("TEST_STEP2: Increase currentMemory value bigger"
                      " than memory by virsh edit")
        edit_cmd = r":%s/{0}/{1}".format(current_mem_xml % current_mem,
                                         current_mem_xml % bigger_mem)
        status = libvirt.exec_virsh_edit(vm_name, [edit_cmd])
        if not status:
            test.fail('Edit guest failed')
        check_mem_config(eval(current_mem_xpath % mem_value))

        test.log.info("TEST_STEP3:Increase memory value to maxMemory by "
                      "cmd virsh edit")
        edit_cmd = r"::%s/{0}/{1}".format(mem_xml % mem_value, mem_xml % max_mem)
        status = libvirt.exec_virsh_edit(vm_name, [edit_cmd])
        if not status:
            test.fail('Edit guest failed')
        check_value = ''
        if mem_config == "without_numa":
            check_value = max_mem
        elif mem_config == "with_numa":
            check_value = mem_value
        check_mem_config(eval(mem_xpath % check_value))

        test.log.info("TEST_STEP4:Increase memory value to maxMemory by "
                      "cmd virsh setmaxmem")
        ret = virsh.setmaxmem(vm_name, max_mem)
        if mem_config == "without_numa":
            libvirt.check_exit_status(ret)
            check_mem_config(eval(mem_xpath % max_mem))
        elif mem_config == "with_numa":
            libvirt.check_exit_status(ret, maxmem_error)
            check_mem_config(eval(mem_xpath % mem_value))

        test.log.info("TEST_STEP5: Start vm and change memory to maxMemory by "
                      "virsh setmaxmem ")
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        cmd_result = virsh.setmaxmem(domain=vm_name, size=max_mem)
        libvirt.check_result(cmd_result, active_maxmem_error)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    def check_mem_config(expect_xpath):
        """
        Check current mem device config

        :params: expect_xpath: expected xpath
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, expect_xpath)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    mem_value = int(params.get("mem_value"))
    bigger_mem = params.get("bigger_mem")
    current_mem = params.get("current_mem")
    max_mem = params.get("max_mem")
    mem_config = params.get("mem_config")
    current_mem_xml = params.get("current_mem_xml")
    mem_xml = params.get("mem_xml")
    current_mem_xpath = params.get("current_mem_xpath")
    mem_xpath = params.get("mem_xpath")
    maxmem_error = params.get("maxmem_error")
    active_maxmem_error = params.get("active_maxmem_error")
    vm_attrs = eval(params.get("vm_attrs"))

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
