#
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

from virttest import libvirt_cgroup
from virttest import utils_libvirtd
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_test import libvirt
from virttest.staging import utils_memory


def run(test, params, env):
    """
    Verify memory tuning parameters take effect
    """

    def adjust_limit_for_memory_page(limit_dict):
        """
        Limit value would be aligned to the default memory page size
        """
        default_page_size = utils_memory.getpagesize()
        return {key: (value // default_page_size * default_page_size)
                if value != -1 else value for key, value in limit_dict.items()}

    def check_limit_by_virsh_memtune(limit_dict):
        """
        Check the memtune limit is expected by virsh memtune
        """
        expected_limit_dict = adjust_limit_for_memory_page(limit_dict)
        for key, value in expected_limit_dict.items():
            actual_value = virsh.memtune_get(vm_name, key)
            if actual_value != value:
                test.fail("memtune limit %s expected %s, but get %s instead"
                          % (key, value, actual_value))

    def set_limit_by_virsh_memtune_in_turn(limit_dict):
        """
        Set memory limit by virsh memtune one by one
        """
        vm_pid = vm.get_pid()
        cg = libvirt_cgroup.CgroupTest(vm_pid)
        for key, value in limit_dict.items():
            options = " --%s %s" % (re.sub('_', '-', key), value)
            result = virsh.memtune_set(vm_name, options, debug=True)
            if not cg.is_cgroup_v2_enabled() and 'hard_limit' == key and 0 == value:
                libvirt.check_result(
                    result, expected_fails=cgroupv1_error_for_zero_hard_limit)
            else:
                libvirt.check_exit_status(result)

    def set_limit_by_virsh_memtune_once(limit_dict):
        """
        Set memory limit by virsh memtune all together
        """
        limit_value_list = [str(limit_dict[limit_name])
                            for limit_name in memtune_cmd_parameter_seq]
        options = " %s" % " ".join(limit_value_list)
        virsh.memtune_set(vm_name, options, debug=True, ignore_status=False)

    def check_limit_by_cgroup(limit_dict):
        """
        Check the memtune limit is expected by cgroup
        """
        adjusted_limit_dict = adjust_limit_for_memory_page(limit_dict)
        expected_limit_dict = {key: (value*1024) if value != -1 else 'max'
                               for key, value in adjusted_limit_dict.items()}
        vm_pid = vm.get_pid()
        cg = libvirt_cgroup.CgroupTest(vm_pid)
        limit_cgroup_dict = cg.get_standardized_cgroup_info("memtune")
        for key in expected_limit_dict:
            if str(expected_limit_dict[key]) != limit_cgroup_dict[key]:
                test.fail("cgroup memory limit %s expected %s, but get %s instead"
                          % (key, expected_limit_dict[key], limit_cgroup_dict[key]))

    def check_limit_by_virsh_dump(limit_dict):
        """
        check the memtune limit is expected by virsh dump
        """
        guest_xml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        if len(set(limit_dict.values())) == 1 and list(limit_dict.values())[0] == -1:
            try:
                if hasattr(guest_xml, "memtune"):
                    test.fail("memtune should not appear in config xml, "
                              "but found memtune XML:\n%s" % guest_xml.memtune)
            except xcepts.LibvirtXMLNotFoundError as detail:
                test.log.debug(
                    "Did not find the memtune xml, error is:%s" % detail)
        else:
            memtune_element = guest_xml.memtune
            for key, value in limit_dict.items():
                if int(getattr(memtune_element, key)) != value:
                    test.fail("memtune limit %s should be %s "
                              "in config xml, but found value:%s"
                              % (key, value, getattr(memtune_element, key)))

    def check_guest_memtune_related_values(limit_dict):
        """
        check memtune by cmd virsh memtune, cgroup value and guest config xml
        """
        test.log.info("Check the memtune value by virsh memtune")
        check_limit_by_virsh_memtune(limit_dict)
        test.log.info("Check the memtune value by cgroup")
        check_limit_by_cgroup(limit_dict)
        test.log.info("Check the memtune value by guest config xml")
        check_limit_by_virsh_dump(limit_dict)

    def run_test():
        """
        Test steps
        """

        test.log.info("TEST_STEP1: Define the guest")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        virsh.define(vmxml.xml, ignore_status=False, debug=True)

        test.log.info("TEST_STEP2: Start the guest")
        if not vm.is_alive():
            vm.start()
        vm_pid = vm.get_pid()
        cg = libvirt_cgroup.CgroupTest(vm_pid)

        test.log.info("TEST_STEP3: Check the memtune setting")
        if "minus" == test_target:
            expected_limit_dict = init_limit_dict
        else:
            expected_limit_dict = max_limit_dict
        check_limit_by_virsh_memtune(expected_limit_dict)

        test.log.info("TEST_STEP4: Change the memtune settings one by one")
        set_limit_by_virsh_memtune_in_turn(target_limit_dict)

        test.log.info("TEST_STEP5: Check the guest status")
        if 'zero' == test_target:
            vm.wait_for_shutdown(shutdown_timeout)
        actual_state = virsh.domstate(
            vm_name, "--reason", debug=True).stdout.strip()
        if 'zero' == test_target:
            expected_state = "shut off (crashed)" if cg.is_cgroup_v2_enabled(
            ) else "running (booted)"
        else:
            expected_state = "running (booted)"
        if expected_state != actual_state:
            test.fail("Guest state should be '%s', "
                      "but get %s instead" % (expected_state, actual_state))

        if 'zero' != test_target:
            test.log.info("TEST_STEP6: For scenarios with running guest, "
                          "check the memtune values")
            check_limit_by_virsh_memtune(target_limit_dict)

            test.log.info("TEST_STEP7: For scenarios with running guest, "
                          "check the cgroup settings")
            check_limit_by_cgroup(target_limit_dict)

            test.log.info("TEST_STEP8: For scenarios with running guest, "
                          "destroy and start the guest")
            vm.destroy()
            vm.start()

            test.log.info("TEST_STEP9: For scenarios with running guest, "
                          "change the memtune setting all together")
            set_limit_by_virsh_memtune_once(target_limit_dict)

            test.log.info("TEST_STEP10-12: For scenarios with running guest, "
                          "check the memtune values")
            check_guest_memtune_related_values(target_limit_dict)

            test.log.info("TEST_STEP13: For scenarios with running guest, "
                          "restart the libvirt daemon")
            utils_libvirtd.libvirtd_restart()

            test.log.info("TEST_STEP14: For scenarios with running guest, "
                          "check the memtune values")
            check_guest_memtune_related_values(target_limit_dict)

    def teardown_test():
        """
        Restore guest config xml.
        """
        test.log.info("TEST_TEARDOWN: Restore guest config xml.")
        bkxml.sync()

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    test_target = params.get('test_target')
    max_limit_dict = eval(params.get('max_limit_dict', '{}'))
    init_limit_dict = eval(params.get('init_limit_dict', '{}'))
    target_limit_dict = eval(params.get('target_limit_dict', '{}'))
    memtune_cmd_parameter_seq = eval(
        params.get('memtune_cmd_parameter_seq', '[]'))
    cgroupv1_error_for_zero_hard_limit = params.get(
        'cgroupv1_error_for_zero_hard_limit', '')
    shutdown_timeout = int(params.get('shutdown_timeout', 0))

    try:
        run_test()

    finally:
        teardown_test()
