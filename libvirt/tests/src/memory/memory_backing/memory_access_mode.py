# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from virttest import virsh
from virttest import test_setup

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_memory

from provider.memory import memory_base


def get_vm_attrs(test, params):
    """
    Get vm attrs.

    :param test: test object
    :param params: VM param
    :return vm_attrs: expected vm attrs dict.
    """
    source_attr = params.get("source_attr", "")
    hugepages_attr = params.get("hugepages_attr", "")
    mem_acccess_attr = params.get("mem_acccess_attr", "")
    numa_attrs = eval(params.get("numa_attrs", '{}'))
    mem_attrs = eval(params.get("mem_attrs", "{}"))

    mb_value = ""
    for item in [source_attr, mem_acccess_attr, hugepages_attr]:
        if item != "":
            mb_value = mb_value + item + ","
    vm_attrs = eval("{'mb':{%s}}" % mb_value[:-1])
    if numa_attrs:
        vm_attrs.update(numa_attrs)
    vm_attrs.update(mem_attrs)
    test.log.debug("Get current vm attrs is :%s" % vm_attrs)

    return vm_attrs


def get_expected_xpath(test, params):
    """
    Get expected xpath.

    :param test: test object
    :param params: VM param
    :return expect_xpath: Get xpath according to the cfg file.
    """
    expect_xpath = []

    source_path = params.get("source_path", '')
    hugepages_path = params.get("hugepages_path", '')
    mem_access_path = params.get("mem_access_path", '')
    numa_access_path = params.get("numa_access_path", '')

    for xpath in [source_path, hugepages_path, mem_access_path,
                  numa_access_path]:
        if xpath != "":
            expect_xpath.append(eval(xpath))
    test.log.debug("Get expected xpath: %s", expect_xpath)
    return expect_xpath


def get_qemu_cmd_line(params):
    """
    Get expected qemu cmd line.

    :param params: VM param
    :return qemu_cmd: Get expected qemu cmd according to the scenarios.
    """
    existed_line, not_existed_line = [], []
    numa_access = params.get("numa_access")
    mem_access = params.get("mem_access")
    mem_pagesize = params.get("mem_pagesize")

    if mem_access == "mem_access_default" and \
            numa_access in ["numa_access_default", "no_numa"] and \
            mem_pagesize == "without_hugepage":
        existed_line += ['"qom-type":"memory-backend-ram"']
        not_existed_line += ['"mem-path":"/var/lib/libvirt/qemu/ram/']

    elif mem_pagesize == "with_hugepage":
        existed_line += ['"qom-type":"memory-backend-file"',
                         '"mem-path":"/dev/hugepages/libvirt/qemu/']

    elif (mem_access in ["mem_access_private", "mem_access_shared",
                         "mem_access_default"] and
          numa_access in ["numa_access_private", "numa_access_shared"]
          and mem_pagesize == "without_hugepage"):

        existed_line += ['"qom-type":"memory-backend-file"',
                         '"mem-path":"/var/lib/libvirt/qemu/ram/']
    elif (mem_access in ["mem_access_private", "mem_access_shared"]
          and numa_access == "numa_access_default" and
          mem_pagesize == "without_hugepage"):

        existed_line += ['"qom-type":"memory-backend-file"',
                         '"mem-path":"/var/lib/libvirt/qemu/ram/']

    elif (mem_access in ["mem_access_private", "mem_access_shared"] and
          numa_access == "no_numa" and mem_pagesize == "without_hugepage"):

        existed_line += ['"qom-type":"memory-backend-file"',
                         '"mem-path":"/var/lib/libvirt/qemu/ram/']

    qemu_cmd = {True: existed_line, False: not_existed_line}

    return qemu_cmd


def get_access_mode(params):
    """
    Get expected access mode according to the scenarios.

    :param params: VM param
    :return: share value.
    """
    share = "false"
    numa_access = params.get("numa_access")
    mem_access = params.get("mem_access")
    source = params.get("source")

    if numa_access == "numa_access_shared":
        share = "true"

    elif mem_access == "mem_access_shared" and \
            numa_access in ["numa_access_default", "no_numa"]:
        share = "true"

    elif source == "memfd" and mem_access == "mem_access_default" and \
            numa_access in ["numa_access_default", "no_numa"]:
        share = "true"

    return share


def run(test, params, env):
    """
    1. Verify memory access setting takes effect
    2. Verify the numa topology access setting takes effect
    against memory backing setting
    """

    def setup_test():
        """
        Setup pagesize
        """
        memory_base.check_mem_page_sizes(
            test, pg_size=int(default_page_size), hp_size=int(set_pagesize))
        hp_cfg.set_kernel_hugepages(set_pagesize, set_pagenum)

    def run_test():
        """
        Define vm and check msg
        Start vm and check config
        Check the qemu cmd line
        Check the access mode
        Login the guest and consume the guest memory
        """
        test.log.info("TEST_STEP1: Define vm and check result")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm_attrs = get_vm_attrs(test, params)
        vmxml.setup_attrs(**vm_attrs)
        test.log.debug("Define vm with %s." % vmxml)
        virsh.undefine(vm.name, debug=True)
        virsh.define(vmxml.xml, debug=True, ignore_status=False)

        test.log.info("TEST_STEP2: Start vm")
        vm.start()

        test.log.info("TEST_STEP3: Check xml config")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, get_expected_xpath(test, params))

        if params.get("source") == "no_source":
            test.log.info("TEST_STEP4: Check the qemu cmd line")
            for existed, item_list in get_qemu_cmd_line(params).items():
                for item in item_list:
                    libvirt.check_qemu_cmd_line(item, expect_exist=existed)
                    test.log.debug("Check %s exist", item)

        test.log.info("TEST_STEP5: Check the memory allocated")
        share_value = get_access_mode(params)
        pattern = pattern_share.format(share_value)

        ret = virsh.qemu_monitor_command(vm_name,
                                         qemu_monitor_cmd,
                                         qemu_monitor_option).stdout_text.strip()
        test.log.debug("Get qemu-monitor-command cmd result:\n%s", ret)
        if not re.search(pattern, ret[ret.index(mem_backend):]):
            test.fail("Expect '%s' exist, but not found." % pattern)
        else:
            test.log.debug("Check '%s' PASS.", pattern)

        test.log.info("TEST_STEP6: Consume the guest memory")
        session = vm.wait_for_login()
        status, output = libvirt_memory.consume_vm_freememory(session)
        if status:
            test.fail("Fail to consume guest memory. Got error:%s" % output)
        session.close()

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        hp_cfg.cleanup()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    pattern_share = params.get('pattern_share')
    mem_backend = params.get("mem_backend")
    qemu_monitor_cmd = params.get('qemu_monitor_cmd')
    qemu_monitor_option = params.get('qemu_monitor_option')
    default_page_size = params.get("default_page_size")
    set_pagesize = params.get("set_pagesize")
    set_pagenum = params.get("set_pagenum")

    hp_cfg = test_setup.HugePageConfig(params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
