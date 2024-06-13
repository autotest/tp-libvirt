# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import re

from avocado.utils import process

from virttest import test_setup
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml

from provider.memory import memory_base


def check_numa_maps(test, params, result):
    """
    Check the numa maps result

    :param params: dict of test parameters
    :param test: test object
    :param result: Numa maps result
    """
    numa_node_mem = []
    kernel_pagesize = []
    set_pagesize = int(params.get("set_pagesize"))
    allocated_mem = params.get("allocated_mem")
    expect_str = params.get("expect_str")
    test.log.debug("Get numa maps result is :%s", result)

    for item in result.split():
        if re.search(r'N(\d+)', item):
            numa_node_mem.append(int(item.split('=')[1]))
        elif item.startswith("kernelpagesize_kB"):
            kernel_pagesize.append(item.split('=')[1])

    if len(kernel_pagesize) > 1:
        test.fail("Kernel pagesize value number should be 1 instead of %s"
                  % (len(kernel_pagesize)))

    huge = result.split()[3]
    if huge != expect_str:
        test.fail("Expect get '%s', but got '%s'" % (expect_str, huge))

    if sum(numa_node_mem)*set_pagesize != int(allocated_mem):
        test.fail("Expect numa node mem get %s, but got %s" % (
            allocated_mem, sum(numa_node_mem)*set_pagesize))

    if int(kernel_pagesize[0]) != set_pagesize:
        test.fail("Kernel pagesize get from numa_maps should be %s intead of %s"
                  % (set_pagesize, kernel_pagesize[0]))


def run(test, params, env):
    """
    1.Verify nodeset config affect the huge page memory allocation
    2.Verify error msg prompts with not existent nodeset
    """

    def setup_test():
        """
        Allocate hugepage memory
        """
        test.log.info("TEST_SETUP: Allocate hugepage memory")
        memory_base.check_mem_page_sizes(
            test, pg_size=int(default_page_size), hp_size=int(set_pagesize))
        hp_cfg = test_setup.HugePageConfig(params)
        hp_cfg.set_kernel_hugepages(set_pagesize, set_pagenum)

    def run_test():
        """
        Define guest, check xml, check memory
        """
        test.log.info("TEST_SETUP1: Define guest")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        result = virsh.define(vmxml.xml, debug=True)

        if globals()["config_error"]:
            if not re.search(globals()["config_error"], result.stderr_text):
                test.fail("Define guest should get error %s instead of %s" % (
                    globals()["config_error"], result))
            else:
                return
        else:
            libvirt.check_exit_status(result)

        test.log.info("TEST_SETUP2: Check xml")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The new xml is:\n%s", vmxml)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, expect_xpath)

        test.log.info("TEST_STEP3: Start vm")
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP4: Check memory")
        pid = process.run('pidof qemu-kvm', shell=True).stdout_text.strip()
        test.log.debug("Get qemu-kvm pid:%s", pid)

        address = process.run('grep -B1 %s /proc/%s/smaps' % (
            allocated_mem, pid), shell=True).stdout_text.split("-")[0]
        test.log.debug("Get address:%s", address)

        result = process.run('grep %s /proc/%s/numa_maps' % (address, pid), shell=True).stdout_text
        check_numa_maps(test, params, result)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        hugepage = test_setup.HugePageConfig(params)
        hugepage.cleanup()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm_attrs = eval(params.get('vm_attrs', '{}'))

    allocated_mem = params.get('allocated_mem')
    default_page_size = params.get("default_page_size")
    set_pagesize = params.get("set_pagesize")
    set_pagenum = params.get("set_pagenum")
    expect_xpath = eval(params.get("expect_xpath"))
    config = params.get("config")
    globals()["config_error"] = params.get("%s_error" % config, None)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
