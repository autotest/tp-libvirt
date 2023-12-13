# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li<nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """
    Verify memory statistics config "period" takes effect with various memory balloon models

    Scenario:
    1.memory balloon models: virtio, virtio-transitional, virtio-non-transitional.
    2.period: int period, undefined.
    3.mem config: mem, current mem.
    """

    def check_same_value(value1, value2, log_msg):
        """
        This function checks whether two values are same or not.

        :param value1: First value to be checked.
        :param value2: Second value to be checked.
        :param log_msg: Log message to display in case of failure.
        :raises: Test.Fail if values are not same.
        """
        if value1 != value2:
            test.fail("The %s should not change from '%s' to '%s'"
                      % (log_msg, value1, value2))

    def get_memory_statistics():
        """
        Get memory statistics by virsh dommemstat

        :return: usable mem, last_update, disk_caches value in result.
        """
        def _get_response():
            res = virsh.dommemstat(vm_name, ignore_status=False,
                                   debug=True).stdout_text
            return re.findall(r'usable (\d+)', res)

        if utils_misc.wait_for(_get_response, 30, 5):
            res = virsh.dommemstat(vm_name, ignore_status=False,
                                   debug=True).stdout_text
            usable_mem = re.findall(r'usable (\d+)', res)[0]
            last_update = re.findall(r'last_update (\d+)', res)[0]
            disk_caches = re.findall(r'disk_caches (\d+)', res)[0]
            return usable_mem, last_update, disk_caches
        else:
            test.fail("Not get virsh dommemstat result in 25s")

    def check_vm_dommemstat(period_config, usable_mem, last_update,
                            disk_caches):
        """
        Check correct vm dommemstat result.

        :params: period_config: period config, undefined , 0 or number
        :params: usable_mem, the usable mem value before consume mem
        :params: last_update, the last update value before consume mem
        :params: disk_caches, the disk caches value before consume mem
        """
        usable_mem_new, last_update_new, disk_caches_new = get_memory_statistics()

        if period_config in ["undefined", "0"]:
            check_same_value(usable_mem_new, usable_mem, "usable value")
            check_same_value(last_update_new, last_update, "last update")
            check_same_value(disk_caches_new, disk_caches, "disk caches")

        elif period_config.isdigit() and period_config != "0":
            if usable_mem_new == usable_mem:
                test.fail("The usable mem should be as '%s' instead of '%s'"
                          % (usable_mem_new, usable_mem))
            if last_update_new <= last_update:
                test.fail("The last update '%s' should bigger than to '%s'"
                          % (last_update_new, last_update))
            if disk_caches_new == disk_caches:
                test.fail("The disk caches mem '%s' should not same as '%s' " %
                          (disk_caches_new, disk_caches))

    def check_xml_and_dommemstat(period_set):
        """
        Check memory balloon xml and dommemstat after consume.

        :params: period_set, the period value.
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Get current guest xml is :%s\n" % vmxml)

        xpath_existed = True
        if period_set in ["undefined", "0"]:
            xpath_existed = False
        if not libvirt_vmxml.check_guest_xml_by_xpaths(
                vmxml, expect_xpath, ignore_status=True) == xpath_existed:
            test.fail("Expect to get '%s' in xml " % expect_xpath)

        usable_mem, last_update, disk_caches = get_memory_statistics()

        guest_session = vm.wait_for_login()
        status, stdout = guest_session.cmd_status_output(mem_operation)
        guest_session.close()
        if status:
            test.fail("Failed to consume memory: %s" % stdout)

        check_vm_dommemstat(period_set, usable_mem, last_update, disk_caches)

    def run_test():
        """
        Define and start guest
        Check memTotal and memory allocation
        """
        test.log.info("TEST_STEP1: Define guest with memballon")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**mem_attrs)
        vmxml.del_device('memballoon', by_tag=True)
        libvirt_vmxml.modify_vm_device(vmxml, 'memballoon', device_dict)

        test.log.info("TEST_STEP2: Start guest ")
        vm.start()

        test.log.info("TEST_STEP3,4,5: Check memory balloon xml and dommemstat "
                      "after consume")
        check_xml_and_dommemstat(period_value_1)

        test.log.info("TEST_STEP6: Change the period setting")
        virsh.dommemstat(vm_name, period_cmd, ignore_status=False, debug=True)

        test.log.info("TEST_STEP7: Repeat TEST_STEP3-5")
        check_xml_and_dommemstat(period_value_2)

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
    mem_operation = params.get("mem_operation")
    period_value_1 = params.get("period_value_1")
    period_value_2 = params.get("period_value_2")
    period_cmd = params.get("period_cmd")
    expect_xpath = eval(params.get("xpath"))

    try:
        run_test()

    finally:
        teardown_test()
