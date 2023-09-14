# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os
import re

from avocado.utils import process

from virttest import utils_libvirtd
from virttest import virsh
from virttest import virt_vm
from virttest import test_setup

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.staging import utils_memory

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    1.Verify different size huge page take effect for guest vm
    2.Verify error msg prompts with invalid huge page size or scarce memory

    Scenario:
    Huge page memory status: 4k, 2M, 1G, 0, scarce.
    """
    def setup_test():
        """
        Set hugepage on host
        """
        test.log.info("TEST_SETUP: Set hugepage on host")
        utils_memory.set_num_huge_pages(int(vm_nr_hugepages))

        hp_cfg = test_setup.HugePageConfig(params)
        hp_cfg.set_kernel_hugepages(set_pagesize, set_pagenum)
        hp_cfg.hugepage_size = mount_size
        hp_cfg.mount_hugepage_fs()
        utils_libvirtd.libvirtd_restart()
        virsh.destroy(vm_name)

    def run_test():
        """
        Define guest, start guest and check mem.
        """
        test.log.info("TEST_STEP1: Define guest")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        cmd_result = virsh.define(vmxml.xml, debug=True)
        if define_error:
            # check scenario: 0
            libvirt.check_result(cmd_result, define_error)
            return

        test.log.info("TEST_STEP2: Start guest")
        try:
            vm.start()
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            test.log.debug("After start vm, get vmxml is :%s", vmxml)
        except virt_vm.VMStartError as details:
            # check scenario: scarce_mem
            if not re.search(start_error, str(details)):
                test.fail("Failed to start guest: {}".format(str(details)))
            else:
                return

        test.log.info("TEST_STEP3: Check the huge page memory usage")
        hp_cfg = test_setup.HugePageConfig(params)
        if int(utils_memory.get_num_huge_pages()) != int(vm_nr_hugepages):
            test.fail("HugePages_Total should be %s instead of %s" % (
                vm_nr_hugepages, utils_memory.get_num_huge_pages()))

        actural_nr = hp_cfg.get_kernel_hugepages(set_pagesize)
        if int(actural_nr) != int(set_pagenum):
            test.fail("nr_hugepages should be %s instead of %s" % (
                set_pagenum, actural_nr))

        if int(utils_memory.get_num_huge_pages_free()) != int(HugePages_Free):
            test.fail("HugePages_Free should be %s instead of %s" % (
                HugePages_Free, utils_memory.get_num_huge_pages_free()))

        free_page_num = process.run(free_hugepages_cmd, shell=True,
                                    verbose=True).stdout_text.strip()
        if int(free_page_num) != int(free_hugepages):
            test.fail("free_hugepages should be %s instead of %s" % (
                free_hugepages, free_page_num))

        test.log.info("TEST_STEP4: Check the huge page memory usage")
        virsh.suspend(vm.name, **VIRSH_ARGS)
        virsh.resume(vm.name, **VIRSH_ARGS)
        vm.wait_for_login().close()

        if os.path.exists(save_file):
            os.remove(save_file)
        virsh.save(vm.name, save_file, **VIRSH_ARGS)
        virsh.restore(save_file, **VIRSH_ARGS)
        vm.wait_for_login().close()

        virsh.managedsave(vm.name, **VIRSH_ARGS)
        vm.start()
        vm.wait_for_login().close()

        virsh.reboot(vm.name, **VIRSH_ARGS)
        session = vm.wait_for_login()

        test.log.info("TEST_STEP5: Verify guest memory could be consumed")
        mem_free = utils_memory.freememtotal(session)
        status, stdout = session.cmd_status_output("swapoff -a; memhog %s" % (
                mem_free - 204800))
        if status:
            raise test.fail("Failed to consume memory:%s" % stdout)

        virsh.destroy(vm.name, **VIRSH_ARGS)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        if os.path.exists(save_file):
            os.remove(save_file)
        hp_cfg = test_setup.HugePageConfig(params)
        hp_cfg.cleanup()

    conf_pagesize = params.get("conf_pagesize")
    kernel_pagesize = process.run("getconf PAGESIZE", shell=True).stdout_text.strip()
    if kernel_pagesize != conf_pagesize:
        test.cancel("The current test does not work with this kernel pagesize.")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    define_error = params.get("define_error")
    vm_nr_hugepages = params.get("vm_nr_hugepages")
    free_hugepages_cmd = params.get("free_hugepages_cmd")
    set_pagesize = params.get("set_pagesize")
    set_pagenum = params.get("set_pagenum")
    HugePages_Free = params.get("HugePages_Free")
    mount_size = params.get("mount_size")
    free_hugepages = params.get("free_hugepages")
    start_error = params.get("start_error")
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    save_file = params.get("save_file", "/tmp/guest.save")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
