# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re
import time

from virttest import virsh
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memballoon
from virttest.staging import utils_memory
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Verify the free page reporting takes effect with various memory balloon models

    Scenario:
    1.memory balloon models: virtio, virtio-transitional, virtio-non-transitional.
    2.free page reporting: undefined, on, off
    3.mem config: mem, current mem.
    """

    def get_memory_statistics():
        """
        Get memory statistics by virsh dommemstat
        :return: rss mem value in result.
        """
        for wait in range(0, 6):
            # wait for max 30s to check dommemstat result.
            time.sleep(5)

            res = virsh.dommemstat(vm_name, ignore_status=False,
                                   debug=True).stdout_text
            if re.findall(r'rss (\d+)', res):
                break

        rss_mem = int(re.findall(r'rss (\d+)', res)[0])
        return rss_mem

    def run_test():
        """
        Define and start guest
        Check memTotal and memory allocation
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
        test.log.debug("After start vm, get xml is:\n%s", vmxml)

        test.log.info("TEST_STEP3: Login the guest and get guest free memory")
        session = vm.wait_for_login()
        free_mem = utils_memory.freememtotal(session)
        session.close()
        test.log.debug('Get free mem in guest', free_mem)

        test.log.info("TEST_STEP4: Get vm memory statistics by virsh dommemstat")
        initial_rss = get_memory_statistics()

        test.log.info("TEST_STEP5: Consume the guest memory")
        consume_mem = free_mem - 102400
        session = vm.wait_for_login()
        libvirt_memory.consume_vm_freememory(session, consume_value=consume_mem)
        session.close()

        test.log.info("TEST_STEP6: Verify the memory usage is increased")
        if fg_config == "fg_on":
            time.sleep(5)
        second_rss = get_memory_statistics()

        if initial_rss > second_rss:
            test.fail("After consuming mem, '%s' mem should be bigger "
                      "than '%s'" % (second_rss, initial_rss))

        test.log.info("TEST_STEP7: Wait for 5 seconds, check memory usage again")
        # if fg_config == "fg_on":
        #     time.sleep(10)
        # third_rss = get_memory_statistics(fg_config)

        # The diff_value is from feature owner experience, there might be some
        # exception at some special case.
        if fg_config in ["fg_undefined", "fg_off"]:
            if abs(get_memory_statistics() - second_rss) > diff_value:
                test.fail("Absolute value of the difference between two"
                          " values %s" % diff_value)
        elif fg_config == "fg_on":
            if not utils_misc.wait_for(
                    lambda: second_rss - get_memory_statistics() > diff_value, 200):
                test.log.debug("Second rss is '%s', Third rss is '%s', difference is '%s'", second_rss, get_memory_statistics(), second_rss-get_memory_statistics())
                test.fail("The difference of two rss value should "
                          "bigger than '%s' " % diff_value)

        test.log.info("TEST_STEP8: Change the current memory allocation")
        virsh.setmem(vm_name, set_used_mem, debug=True, ignore_status=False)

        test.log.info("TEST_STEP9: Check the memory allocation config")
        if not utils_misc.wait_for(lambda: libvirt.check_result(virsh.dominfo(vm_name, debug=True), expected_match=dominfo_check) is None, 30, first=5):
            test.fail("Expected get '%s' in virsh dominfo from the "
                      "above result" % dominfo_check)

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
    set_used_mem = params.get("set_used_mem")
    dominfo_check = params.get("dominfo_check")
    fg_config = params.get("fg_config")
    diff_value = int(params.get("diff_value"))

    try:
        run_test()

    finally:
        teardown_test()
