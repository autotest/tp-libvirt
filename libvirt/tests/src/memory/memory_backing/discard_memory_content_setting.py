# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import test_setup
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    1. Verify memory discard setting takes effect
    2. Verify the numa topology discard setting takes effect
    against memory backing setting
    """

    def get_init_vm_attrs():
        """
        Get vm attrs.

        :return vm_attrs: expected vm attrs dict.
        """
        mb_value = ""
        for item in [source_attr, hugepages_attr, mem_discard_attr]:
            if item != "":
                mb_value = mb_value + item + ","

        vm_attrs = eval("{'mb':{%s}}" % mb_value[:-1])

        if numa_attrs:
            vm_attrs.update(numa_attrs)
        vm_attrs.update(mem_attrs)
        test.log.debug("Get current vm attrs is :%s", vm_attrs)

        return vm_attrs

    def get_expected_xpath():
        """
        Get expected xpath.

        :return expect_xpath: Get xpath according to the cfg file.
        """
        expect_xpath = []
        for xpath in [numa_path, source_path, hugepages_path, mem_xpath,
                      mem_discard_path]:
            if xpath != "":
                expect_xpath.append(eval(xpath))
        test.log.debug("Get expected xpath: %s", expect_xpath)
        return expect_xpath

    def get_expected_discard():
        """
        Get expected discard value.

        :return expected_discard : the expected discard value, True or False
        :return existed: expected the expected_discard exist or not
        """
        expected_discard, existed = "true", True

        if source in ["file", "hugepaged_file"]:
            if mem_discard == "mem_discard_yes" and numa_discard in \
                    ["numa_discard_yes", "numa_discard_not_defined", "no_numa"]:
                expected_discard = "true"

            elif mem_discard == "mem_discard_not_defined" and \
                    numa_discard == "numa_discard_yes":
                expected_discard = "true"

            elif mem_discard in ["mem_discard_yes", "mem_discard_not_defined"] \
                    and numa_discard == "numa_discard_no":
                # Check libvirt version for numa_discard_no scenario
                if libvirt_version.version_compare(9, 0, 0):
                    expected_discard = "false"
                else:
                    existed = False
            else:
                existed = False

        elif source in ["anonymous", "memfd"]:
            existed = False

        test.log.debug("Get expected discard:%s, discard existed: %s",
                       expected_discard, existed)
        return expected_discard, existed

    def setup_test():
        """
        Prepare memory device
        """
        test.log.info("TEST_SETUP: Set hugepage.")
        hp_cfg.set_kernel_hugepages(set_pagesize, set_pagenum)

    def run_test():
        """
        """
        test.log.info("TEST_STEP1: Define vm.")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_attrs = get_init_vm_attrs()
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        test.log.debug("After define vm, vm xml is:\n:%s", vmxml)

        test.log.info("TEST_STEP2: Start vm.")
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP3: Check the xml config.")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        expect_xpath = get_expected_xpath()
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, expect_xpath)

        test.log.info("TEST_STEP4: Check the qemu cmd line.")
        expected_discard, existed = get_expected_discard()
        libvirt.check_qemu_cmd_line(qemu_line % expected_discard,
                                    expect_exist=existed)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        hp_cfg.cleanup()
        bkxml.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    qemu_line = params.get("qemu_line")
    numa_discard = params.get("numa_discard")
    mem_discard = params.get("mem_discard")
    set_pagesize = params.get("set_pagesize")
    set_pagenum = params.get("set_pagenum")

    source_attr = params.get("source_attr", '')
    hugepages_attr = params.get("hugepages_attr", '')
    mem_discard_attr = params.get("mem_discard_attr", '')
    numa_attrs = eval(params.get("numa_attrs", '{}'))
    mem_attrs = eval(params.get("mem_attrs", '{}'))
    mem_discard_attr = params.get("mem_discard_attr", '')

    numa_path = params.get("numa_path", '')
    source_path = params.get("source_path", '')
    hugepages_path = params.get("hugepages_path", '')
    mem_xpath = params.get("mem_xpath", '')
    mem_discard_path = params.get("mem_discard_path", '')
    source = params.get("source", '')
    hp_cfg = test_setup.HugePageConfig(params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
