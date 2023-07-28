# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import utils_misc
from virttest import virsh
from virttest import test_setup

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_memory
from virttest.staging import utils_memory


def run(test, params, env):
    """
    1.Verify libvirt could lock memory for guest vm.
    2.Verify libvirt could enable|disable KSM
    """
    def get_init_vm_attrs():
        """
        Get vm attrs.

        :return expected vm attrs dict.
        """
        mb_value = ""
        for item in [lock_dict, ksm_dict, hugepages_dict]:
            if item != "":
                mb_value += "%s," % item
        vm_attrs = eval("{'mb':{%s}}" % mb_value[:-1])

        if tune_dict:
            vm_attrs.update(tune_dict)
        vm_attrs.update(mem_attrs)

        test.log.debug("Get current vm attrs is :%s" % vm_attrs)
        return vm_attrs

    def setup_test():
        """
        Prepare init xml
        """
        test.log.info("TEST_SETUP: Set hugepage")
        hp_cfg = test_setup.HugePageConfig(params)
        hp_cfg.set_kernel_hugepages(pagesize, pagenum)

    def run_test():
        """
        Do lifecycle and check xml
        """
        test.log.info("TEST_STEP1: Define vm")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_attrs = get_init_vm_attrs()
        vmxml.setup_attrs(**vm_attrs)
        test.log.debug("Define vm with %s." % vmxml)
        virsh.define(vmxml.xml, debug=True, ignore_status=False)

        test.log.info("TEST_STEP2: Get the locked and unevictable memory pages")
        lock_pages = int(utils_memory.read_from_vmstat("nr_mlock"))
        nr_unevictable = int(utils_memory.read_from_vmstat("nr_unevictable"))

        test.log.info("TEST_STEP3: Start vm")
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()

        test.log.info("TEST_STEP4: Check the qemu cmd line")
        for check_item in qemu_line:
            if ksm_config == "ksm_default" and check_item == "mem-merge=off":
                existed = False
            else:
                existed = expect_exist
            libvirt.check_qemu_cmd_line(check_item,
                                        expect_exist=bool(existed))

        test.log.info("TEST_STEP5: Consume the guest memory")
        status, output = libvirt_memory.consume_vm_freememory(session)
        if status:
            test.fail("Fail to consume guest memory. Error:%s" % output)
        session.close()

        test.log.info("TEST_STEP6:  Check the locked memory pages")
        lock_pages_2 = int(utils_memory.read_from_vmstat("nr_mlock"))
        nr_unevictable_2 = int(utils_memory.read_from_vmstat("nr_unevictable"))

        if lock_config == "lock_default":
            if lock_pages != lock_pages_2:
                test.fail("Nr_mlock should not change to:%d" % lock_pages_2)
            if nr_unevictable != nr_unevictable_2:
                test.fail("Unevictable should not change to:%d" % nr_unevictable_2)
            return
        else:
            if page_config == "page_default":
                if (nr_unevictable_2 - nr_unevictable)*4 < mem_value:
                    test.fail("Unevictable memory pages should less than %d" % mem_value)
                if (lock_pages_2 - lock_pages)*4 < mem_value:
                    test.fail("Locked memory pages should less than %d" % mem_value)
            elif page_config == "hugepage":
                if (nr_unevictable_2 - nr_unevictable) < 0:
                    test.fail("After consuming mem, the current unevictable "
                              "memory '%s' should bigger than '%s'" %
                              (nr_unevictable_2, nr_unevictable))
                if (lock_pages_2 - lock_pages) < 0:
                    test.fail("After consuming mem, the current lock pages '%s'"
                              " should bigger than '%s'" % (lock_pages_2, lock_pages))

        test.log.info("TEST_STEP7: Check the qemu process limit of memory lock")
        soft, hard = libvirt_memory.get_qemu_process_memlock_limit()

        if (soft, hard) != (process_limit, process_limit):
            test.fail("Not get expected limit:'%s' " % (process_limit+process_limit))

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

    pagesize = params.get("pagesize")
    pagenum = params.get("pagenum")
    lock_config = params.get("lock_config", '')
    page_config = params.get("page_config")
    ksm_config = params.get("ksm_config")
    expect_exist = params.get("expect_exist")
    qemu_line = eval(params.get("qemu_line"))
    hard_limit = int(params.get("hard_limit", 0))
    mem_value = int(params.get("mem_value", ''))

    lock_dict = params.get("lock_dict", "")
    tune_dict = eval(params.get("tune_dict", "{}"))
    ksm_dict = params.get("ksm_dict", "")
    hugepages_dict = params.get("hugepages_dict", "")
    mem_attrs = eval(params.get("mem_attrs", ""))

    convert_limit = utils_misc.normalize_data_size('%dG' % hard_limit, 'kb')
    process_limit = params.get("process_limit", str(int(float(convert_limit))))

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
