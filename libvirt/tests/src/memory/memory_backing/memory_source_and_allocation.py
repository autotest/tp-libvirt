# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import json
import re

from avocado.utils import process

from virttest import utils_misc
from virttest import virsh
from virttest import test_setup

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_memory


def run(test, params, env):
    """
    Verify different kinds of memory source type work well.
    """
    def get_vm_attrs():
        """
        Get vm attrs.
        :return vm_attrs: expected vm attrs dict.
        """
        mb_value = ""
        for item in [source_attr, hugepages_attr, alloc_attr]:
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
        for xpath in [source_path, hugepages_path, numa_path, mem_xpath,
                      mem_discard_path]:
            if xpath != "":
                expect_xpath.append(eval(xpath))
        test.log.debug("Get expected xpath: %s", expect_xpath)
        return expect_xpath

    def check_allocated_mem(params, result):
        """
        Check the memory allocated from the host.

        :param params: Dictionary with the test parameters
        :param result: memory allocated result.
        """
        alloc_mode = params.get("allocation_mode")
        pagesize_set = params.get("pagesize")

        rss_value = int(re.findall(r'Rss:\s+(\w+)', result)[0])
        private_hugetlb = int(re.findall(r'Private_Hugetlb:\s+(\w+)', result)[0])

        if pagesize_set == "page_default" and alloc_mode == "ondemand":
            if rss_value < 0 or rss_value > mem_value:
                test.fail("Rss should be more than 0 and less than '%s', "
                          "but got '%s' " % (mem_value, rss_value))
            if private_hugetlb != 0:
                test.fail("Private_Hugetlb should be 0 instead of %s" % private_hugetlb)

        elif pagesize_set == "hugepage" and alloc_mode == "ondemand":
            if rss_value != mem_value:
                test.fail("Rss should be %s instead of %s" % (mem_value, rss_value))
            if private_hugetlb != 0:
                test.fail("Private_Hugetlb should be 0 instead of %s" %
                          private_hugetlb)
        elif pagesize_set == "hugepage" and alloc_mode == "immediate":
            if rss_value != 0:
                test.fail("Rss should be 0 instead of %s" % rss_value)
            if private_hugetlb != mem_value:
                test.fail("Private_Hugetlb should be %s instead of %s" %
                          (mem_value, private_hugetlb))

    def check_backend_and_path(mem_name):
        """
        Check backend type and memory path

        :param mem_name: memory name
        """
        result = virsh.qemu_monitor_command(
            vm_name, check_thread % (mem_name, 'type'), debug=True,
            ignore_status=False).stdout_text
        backend_type = json.loads(result)['return']

        res = virsh.qemu_monitor_command(
            vm_name, check_thread % (mem_name, 'mem-path'), debug=True).stdout_text
        # Check backend type
        if expected_backend_type != backend_type:
            test.fail("Expected backend type '%s', but got '%s'" % (
                expected_backend_type, backend_type))
        # Check memory path
        if 'return' in json.loads(res):
            mem_path = json.loads(res)['return']
            if expected_mem_path not in mem_path:
                test.fail("Expected memory path '%s', but got '%s'" % (
                    expected_mem_path, mem_path))

        test.log.debug("Get correct backend type '%s' and mem path '%s'",
                       expected_backend_type, expected_mem_path)

    def check_hugepage(params, mem_name):
        """
        Check hugepage setting.

        :param params: Dictionary with the test parameters
        :param mem_name: memory name
        """

        pagesize_set = params.get("pagesize")
        source_set = params.get("source", '')
        memory_value = params.get("mem_value")
        alloc_mode = params.get("allocation_mode")

        if source_set == "memfd" and pagesize_set == "hugepage" \
                and alloc_mode == "immediate":

            result = virsh.qemu_monitor_command(
                vm_name, check_thread % (mem_name, 'hugetlb'), debug=True).stdout_text
            hugepage = str(json.loads(result)['return'])

            result = virsh.qemu_monitor_command(
                vm_name, check_thread % (mem_name, 'size'), debug=True).stdout_text
            hugepage_size = str(json.loads(result)['return'])

            if hugepage != 'True':
                test.fail("Expect to get hugepage 'True', but got '%s'" % hugepage)
            if int(hugepage_size) != int(memory_value) * 1024:
                test.fail("Expect to get hugepage size '%s', but got '%s'" % (
                    hugepage_size, memory_value))
            test.log.debug("Get correct hugepage '%s' and hugepage_size '%s'",
                           hugepage, hugepage_size)

    def setup_test():
        """
        Setup pagesize
        """
        hp_cfg.set_kernel_hugepages(set_pagesize, set_pagenum)

    def run_test():
        """
        Define vm and check msg
        Start vm and check config
        Check the qemu
        Check the memory allocated
        Login the guest and consume the guest memory
        """
        test.log.info("TEST_STEP1: Define vm and check result")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm_attrs = get_vm_attrs()
        vmxml.setup_attrs(**vm_attrs)
        test.log.debug("Define vm with %s.", vmxml)
        virsh.undefine(vm.name, debug=True)
        cmd_result = virsh.define(vmxml.xml, debug=True)
        if error_msg:
            libvirt.check_result(cmd_result, error_msg)
            return
        else:
            libvirt.check_exit_status(cmd_result)

        test.log.info("TEST_STEP2: Start vm")
        vm.start()

        test.log.info("TEST_STEP3: Check xml config")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, get_expected_xpath())

        test.log.info("TEST_STEP4: Check the pre-allocation setting")
        result = virsh.qemu_monitor_command(
            vm.name, monitor_cmd, monitor_option, debug=True,
            ignore_status=False).stdout
        memory_name = re.findall(memory_name_pattern, result)[0]

        test.log.debug("Get memory name '%s'", memory_name)
        if allocation_mode == "immediate":
            if not re.search(prealloc, result):
                test.fail("Expected '%s' from '%s'" % (prealloc, result))

            test.log.info("TEST_STEP5: Check the threads setting")
            result = virsh.qemu_monitor_command(
                vm_name, check_thread % (memory_name, 'prealloc-threads')).stdout_text
            threads_value = str(json.loads(result)['return'])
            if threads_value != threads:
                test.fail("Expected threads value '%s' instead of '%s'" % (threads, threads_value))

        test.log.info("TEST_STEP6: Check the backend type and path")
        check_backend_and_path(memory_name)

        test.log.info("TEST_STEP7: Check huge page setting")
        check_hugepage(params, memory_name)

        test.log.info("TEST_STEP8: Check the memory allocated")
        session = vm.wait_for_login()
        if utils_misc.wait_for(
                lambda: process.run(
                    check_allocated_cmd, shell=True).exit_status == 0, 5):
            cmd_result = process.run(check_allocated_cmd, shell=True).stdout_text
            check_allocated_mem(params, cmd_result)

        test.log.info("TEST_STEP9: Consume the guest memory")
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
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    error_msg = params.get("error_msg")
    allocation_mode = params.get("allocation_mode")
    pagesize = params.get("pagesize")
    prealloc = params.get("prealloc")
    check_thread = params.get("check_thread")
    memory_name_pattern = params.get("memory_name_pattern")
    check_allocated_cmd = params.get("check_allocated_cmd")
    numa_attrs = eval(params.get("numa_attrs", '{}'))
    set_pagesize = params.get("set_pagesize")
    set_pagenum = params.get("set_pagenum")
    source_attr = params.get("source_attr", "")
    mem_value = int(params.get("mem_value", 0))
    hugepages_attr = params.get("hugepages_attr", "")
    alloc_attr = params.get("alloc_attr", "")
    mem_attrs = eval(params.get("mem_attrs", "{}"))
    source = params.get("source", '')
    threads = params.get("threads", '')
    vm = env.get_vm(vm_name)

    numa_path = params.get("numa_path", '')
    source_path = params.get("source_path", '')
    hugepages_path = params.get("hugepages_path", '')
    mem_xpath = params.get("mem_xpath", '')
    expected_backend_type = params.get("expected_backend_type", '')
    expected_mem_path = params.get("expected_mem_path", '')
    mem_discard_path = params.get("mem_discard_path", '')
    monitor_option = params.get("monitor_option", '')
    monitor_cmd = params.get("monitor_cmd", '')
    hp_cfg = test_setup.HugePageConfig(params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
