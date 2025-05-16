# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re
import time

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memballoon
from virttest.staging import utils_memory
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Verify the freepagereporting takes effect with various memory balloon models
    """
    def get_rss_mem_list(timeout=30):
        """"
        Get rss memory list from virsh dommemstat cmd per second

        :param timeout: timeout of getting rss memory list from virsh dommemstat cmd
        :return: rss memory list
        """
        current = 0
        rss_mem_list = []
        while current < timeout:
            time.sleep(1)
            rss_mem_list.append(get_rss_mem())
            current += 1
        return rss_mem_list

    def get_rss_mem():
        """"
        Get rss memory from virsh dommemstat cmd
        """
        output = virsh.dommemstat(vm_name, **virsh_dargs).stdout_text
        match = re.search(r"rss\s+(\d+)", output)
        return int(match.group(1))

    def run_test():
        """
        Test steps
        1. Define guest
        2. Start the domain
        3. Check the memory usage of the guest on host
        4. Login the guest and consume the guest memory
        5. Verify the memory usage is increased after consuming more memory on host
        6. Check the memory after memory consumption
        """
        test.log.info("TEST_STEP1: Define guest")
        vmxml.del_device('memballoon', by_tag=True)
        mem_balloon = memballoon.Memballoon()
        mem_balloon.setup_attrs(**memballoon_dict)
        vmxml.devices = vmxml.devices.append(mem_balloon)
        vmxml.setup_attrs(**mem_attrs)
        vmxml.sync()

        test.log.info("TEST_STEP2: Start the domain")
        vm.start()

        test.log.info("TEST_STEP3: Check the memory usage of the guest on host")
        session = vm.wait_for_login()
        init_rss = get_rss_mem()

        test.log.info(
            "TEST_STEP4: Login the guest and consume the guest memory")
        rss_mem_thread = utils_misc.InterruptedThread(get_rss_mem_list)
        rss_mem_thread.start()
        free_mem = utils_memory.freememtotal(session)
        session.cmd_status("swapoff -a")
        session.cmd_status(mem_consume_cmd % (free_mem - unconsumed_mem))
        session.close()
        rss_mem_list = rss_mem_thread.join()
        test.log.debug(
            "The rss memory list is %s during guest consumption." % rss_mem_list)

        test.log.info(
            "TEST_STEP5: Verify the memory usage is increased after consuming more memory on host")
        second_rss_mem = max(rss_mem_list)
        if second_rss_mem < init_rss:
            test.fail("Expected rss memory after consuming %s is larger than %s" % (
                second_rss_mem, init_rss))

        test.log.info("TEST_STEP6: Check the memory after memory consumption")
        third_rss_mem = rss_mem_list[-1]
        rss_mem_changed = second_rss_mem - third_rss_mem
        if max_memory_difference and abs(rss_mem_changed) > max_memory_difference:
            test.fail("Expected rss memory changed lower than %s, but got %s" % (
                max_memory_difference, abs(rss_mem_changed)))
        if min_memory_difference and rss_mem_changed < min_memory_difference:
            test.fail("Expected rss memory changed larger than %s, but got %s" % (
                min_memory_difference, rss_mem_changed))

        test.log.info(
            "TEST_STEP7: Change the current memory allocation of the guest.")
        result = virsh.setmem(domain=vm_name, size=set_memory, **virsh_dargs)
        libvirt.check_exit_status(result)

        test.log.info(
            "TEST_STEP8: Check the memory allocation config by virsh dominfo.")
        if not utils_misc.wait_for(lambda: vm.get_used_mem() == int(set_memory), timeout=10):
            test.fail("Expect used memory of dominfo is %s, but got %s" % (
                set_memory, vm.get_used_mem()))
        max_mem = vm.get_max_mem()
        if max_mem != int(mem_value):
            test.fail("Expected max memory of dominfo is %s, but got %s" %
                      (mem_value, max_mem))

    def teardown_test():
        """
        Clean test environment.
        """
        test.log.info("TEST_TEARDOWN: Clean up environment.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    memballoon_dict = eval(params.get('memballoon_dict', '{}'))
    mem_attrs = eval(params.get('mem_attrs', '{}'))
    virsh_dargs = {"debug": True, "ignore_status": True}
    max_memory_difference = int(params.get('max_memory_difference', '0'))
    min_memory_difference = int(params.get('min_memory_difference', '0'))
    set_memory = params.get('set_memroy')
    mem_value = params.get('mem_value')
    mem_consume_cmd = params.get('mem_consume_cmd')
    unconsumed_mem = int(params.get('unconsumed_mem'))

    try:
        run_test()

    finally:
        teardown_test()
