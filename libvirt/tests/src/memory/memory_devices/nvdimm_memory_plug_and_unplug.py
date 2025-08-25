# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os
import re

from avocado.utils import process

from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.memory import memory_base


def run(test, params, env):
    """
    Verify nvdimm memory device plug and unplug behaviors
    """
    def group_nvdimm_basic_property():
        """
        Group nvdimm basic property from params
        """
        pattern = re.compile(r'^(nvdimm\d+)_(path|size|unit)')
        grouped = {}
        for k, v in sorted(params.items()):
            match = pattern.match(k)
            if match:
                grouped.setdefault(match.group(1), []).append(v)
        return grouped

    def check_mem_and_mem_device_xml(mem_alloc_xpath=None, mem_device_xpath_list=None):
        """
        Check the memory allocation and memory device xml config

        :param mem_alloc_xpath: xpath to check the memory allocation config
        :param mem_device_xpath_list: list of xpaths to check the memory device config
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Current guest config xml is:\n%s", vmxml)

        if mem_alloc_xpath:
            libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, mem_alloc_xpath)

        if mem_device_xpath_list:
            memory_devices = vmxml.devices.by_device_tag('memory')
            for index, memory_device in enumerate(memory_devices):
                libvirt_vmxml.check_guest_xml_by_xpaths(memory_device, mem_device_xpath_list[index])

    def setup_test():
        """
        Set up nvdimm backend files
        """
        nonlocal property_dict
        property_dict = group_nvdimm_basic_property()
        for p_list in property_dict.values():
            process.run(f"truncate -s {p_list[1]}{p_list[2]} {p_list[0]}", verbose=True)

    def operate_nvdimms(nvdimm_device_list, is_plug, plug_option, error_msg=None, expected_event=None):
        """
        Plug or unplug nvdimm memory devices to vm

        :param nvdimm_device_list: list of nvdimm devices to be plugged or unplugged
        :param is_plug: True or False, if plug or unplug
        :param plug_option: flagstr for virsh attach(detach)-device command
        :param error_msg: error message if operation fails
        :param expected_event: expected event during operation
        """
        wait_event = True if expected_event else False
        for nvdimm_dev in nvdimm_device_list:
            nvdimm_device = Memory()
            nvdimm_device.setup_attrs(**nvdimm_dev)
            if is_plug:
                ret = virsh.attach_device(vm_name, nvdimm_device.xml, wait_for_event=wait_event,
                                          event_type=expected_event, flagstr=plug_option, **virsh_args)
            else:
                ret = virsh.detach_device(vm_name, nvdimm_device.xml, wait_for_event=wait_event,
                                          event_type=expected_event, flagstr=plug_option, **virsh_args)
            libvirt.check_result(ret, error_msg)

    def check_audit_log(audit_cmd, expected_msg):
        """
        Check audit log for expected message

        :param audit_cmd: command to get audit log
        :param expected_msg: expected message in audit log
        """
        audit_result = process.run(audit_cmd, shell=True)
        libvirt.check_result(audit_result, expected_match=expected_msg)
        test.log.debug(f"Check audit log with {expected_msg} successfully.")

    def check_libvirtd_log(expected_msg):
        """
        Check libvirtd log for expected message

        :param expected_msg: expected message in libvirtd log
        """
        result = utils_misc.wait_for(
            lambda: libvirt.check_logfile(expected_msg, libvirt_log_path), timeout=20)
        if not result:
            test.fail(f"Not found expected msg:{expected_msg} in {libvirt_log_path}")

    def run_test():
        """
        Test steps:
        1. Define the guest
        2. Cold-plug nvdimm devices
        3. Cold-unplug nvdimm devices
        4: Start guest
        5: Check the memory allocation and memory device by virsh dump
        6: Check the memory info by virsh dominfo
        7: Hot-plug nvdimm devices
        8: Check the audit log
        9: Check the libvirtd log
        10: Check the memory allocation and memory device config
        11: Hot-unplug nvdimm devices
        """
        test.log.info("TEST_STEP1: Define the guest")
        memory_base.define_guest_with_memory_device(params, init_nvdimms, vm_attrs)
        if init_xpath:
            check_mem_and_mem_device_xml(mem_device_xpath_list=init_xpath)

        if cold_plug_nvdimms:
            test.log.info("TEST_STEP2: Cold-plug nvdimm devices")
            operate_nvdimms(cold_plug_nvdimms, True, "--config", error_msg)
            if coldplug_xpath:
                check_mem_and_mem_device_xml(mem_device_xpath_list=coldplug_xpath)

        if cold_unplug_nvdimms:
            test.log.info("TEST_STEP3: Cold-unplug nvdimm devices")
            operate_nvdimms(cold_unplug_nvdimms, False, "--config", error_msg)

        if "hot" in plug_type:
            test.log.info("TEST_STEP4: Start guest")
            vm.start()

            if test_case == "positive":
                exp_startup_mem = int(memory_size) + sum(init_nvdimms_size)
                exp_startup_curr_mem = int(curr_memory_size)

                test.log.info("TEST_STEP5: Check the memory allocation and memory device by virsh dump")
                memory_alloc_xpath = eval(memory_xpath.format(exp_startup_mem))
                check_mem_and_mem_device_xml(memory_alloc_xpath, startup_xpath)

                test.log.info("TEST_STEP6: Check the memory info by virsh dominfo")
                memory_base.check_dominfo(vm, test, str(exp_startup_mem), str(exp_startup_curr_mem))

            if hot_plug_nvdimms:
                test.log.info("TEST_STEP7: Hot-plug nvdimm devices")
                operate_nvdimms(hot_plug_nvdimms, True, "", error_msg, plug_event)

                if test_case == "positive":
                    exp_hotplug_mem = int(memory_size) + sum(init_nvdimms_size + hotplug_nvdimms_size)

                    test.log.info("TEST_STEP8: Check the audit log")
                    audit_check_msg = audit_check.format(exp_startup_mem, exp_hotplug_mem)
                    check_audit_log(audit_cmd, audit_check_msg)

                    test.log.info("TEST_STEP9: Check the libvirtd log")
                    check_libvirtd_log(libvirt_log_check)

                    test.log.info("TEST_STEP10: Check the memory allocation and memory device config")
                    memory_alloc_xpath = eval(memory_xpath.format(exp_hotplug_mem))
                    check_mem_and_mem_device_xml(memory_alloc_xpath, hotplug_xpath)

            if hot_unplug_nvdimms:
                test.log.info("TEST_STEP11: Hot-unplug nvdimm devices")
                operate_nvdimms(hot_unplug_nvdimms, False, "", error_msg)

    def teardown_test():
        """
        Clean up environment after test
        1. Remove nvdimm backed file
        2. Restore domain xml
        """
        nonlocal property_dict
        for p_list in property_dict.values():
            if os.path.exists(p_list[0]):
                os.remove(p_list[0])
        bkxml.sync()

    virsh_args = {'debug': True, 'ignore_status': True}
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    property_dict = None
    vm_attrs = eval(params.get("vm_attrs"))
    init_nvdimms = eval(params.get("init_nvdimms", "[]"))
    cold_plug_nvdimms = eval(params.get("cold_plug_nvdimms", "[]"))
    cold_unplug_nvdimms = eval(params.get("cold_unplug_nvdimms", "[]"))
    hot_plug_nvdimms = eval(params.get("hot_plug_nvdimms", "[]"))
    hot_unplug_nvdimms = eval(params.get("hot_unplug_nvdimms", "[]"))
    error_msg = params.get("error_msg", None)
    plug_type = params.get("plug_type")
    test_case = params.get("test_case")
    memory_size = params.get("memory_size")
    curr_memory_size = params.get("curr_memory_size")
    memory_xpath = params.get("memory_xpath", "[]")
    init_nvdimms_size = eval(params.get("init_nvdimms_size", "[]"))
    hotplug_nvdimms_size = eval(params.get("hotplug_nvdimms_size", "[]"))
    plug_event = params.get("plug_event")
    audit_cmd = params.get("audit_cmd")
    audit_check = params.get("audit_check")
    libvirt_log_check = params.get("libvirt_log_check")
    libvirt_log_path = os.path.join(test.debugdir, "libvirtd.log")
    init_xpath = eval(params.get("init_xpath", "[]"))
    coldplug_xpath = eval(params.get("coldplug_xpath", "[]"))
    startup_xpath = eval(params.get("startup_xpath", "[]"))
    hotplug_xpath = eval(params.get("hotplug_xpath", "[]"))

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
