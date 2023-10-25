# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memballoon
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Verify no error msg prompts without guest memory balloon driver.
    Scenario:
    1.memory balloon models: virtio, virtio-transitional, virtio-non-transitional.
    """

    def remove_module(module_name):
        """
        Remove specific module.

        :params module_name: name of one module.
        """
        session = vm.wait_for_login()
        status, stdout = session.cmd_status_output(rm_module % module_name)
        if status != 0:
            test.log.failed("Fail to remove virtio_balloon module:\n%s" % stdout)

        status, stdout = session.cmd_status_output(check_module % module_name)
        if stdout:
            test.fail("virtio_balloon module should be removed: '%s'" % stdout)
        session.close()

    def run_test():
        """
        Define and start guest
        Check No error msg prompts without guest memory balloon driver.
        """
        test.log.info("TEST_STEP1: Define guest")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        vmxml.del_device('memballoon', by_tag=True)
        mem_balloon = memballoon.Memballoon()
        mem_balloon.setup_attrs(**device_dict)
        vmxml.devices = vmxml.devices.append(mem_balloon)

        vmxml.setup_attrs(**mem_attrs)
        vmxml.sync()

        test.log.info("TEST_STEP2: Start guest ")
        if not vm.is_alive():
            vm.start()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("After define vm, get vmxml is:\n%s", vmxml)

        test.log.info("TEST_STEP3: Remove virtio_balloon module in guest")
        remove_module(module)

        test.log.info("TEST_STEP4: Change guest current memory allocation")
        result = virsh.setmem(domain=vm_name, size=set_mem, debug=True)
        libvirt.check_exit_status(result)

        test.log.info("TEST_STEP5: Check memory allocation is not changed")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, expect_xpath)

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

    mem_attrs = eval(params.get("mem_attrs", "{}"))
    device_dict = eval(params.get("device_dict", "{}"))
    set_mem = int(params.get("set_mem"))
    module = params.get("module")
    check_module = params.get("check_module")
    rm_module = params.get("rm_module")
    expect_xpath = eval(params.get("expect_xpath", '{}'))

    try:
        run_test()

    finally:
        teardown_test()
