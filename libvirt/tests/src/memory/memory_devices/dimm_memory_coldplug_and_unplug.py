# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from avocado.utils import memory

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.memory import memory_base


def run(test, params, env):
    """
    Verify dimm memory device cold-plug and cold-unplug with different configs
    """
    def setup_test():
        """
        Adjust the configuration parameters for the test environment
        """
        def _remove_empty_entries(config_dict):
            """
            Remove empty entries of dictionary

            :param config_dict: the target dictionary
            """
            for key in list(config_dict.keys()):
                if not config_dict[key]:
                    del config_dict[key]

        def _parse_config_string(config_string, page_size):
            """
            Parses and evaluates a configuration string into Python data structure

            :param config_string: the string of config
            :param page_size: page size in KiB
            :return: the processed config of python data structure
            """
            return (
                eval(config_string % page_size) if config_string.count(
                    "%") else eval(config_string)
            )

        nonlocal init_dimm_dict, plug_dimm_dict, init_xpath_list, plug_xpath_list
        page_size = memory.get_page_size() // 1024
        init_dimm_dict = _parse_config_string(init_dimm_dict, page_size)
        init_xpath_list = _parse_config_string(init_xpath_list, page_size)
        _remove_empty_entries(init_dimm_dict)
        if with_plug_dimm:
            plug_dimm_dict = _parse_config_string(plug_dimm_dict, page_size)
            plug_xpath_list = _parse_config_string(plug_xpath_list, page_size)
            _remove_empty_entries(plug_dimm_dict)

    def check_dimm_mem_device_xml(*dimm_xpaths):
        """
        Check dimm memory devices xml against expected xpaths

        :param dimm_xpaths: list of expected xpaths
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Current guest config xml is:\n %s", vmxml)
        memory_devices = vmxml.devices.by_device_tag('memory')
        expect_dimm_num = len(dimm_xpaths)
        actual_dimm_num = len(memory_devices)

        if actual_dimm_num != expect_dimm_num:
            test.fail("Expected %d dimm mem devices, but found %d" %
                      (expect_dimm_num, actual_dimm_num))

        def _all_xpaths_match(dimm_mem, xpath_list):
            """
            Check if all xpaths in the list match the memory device XML

            :param dimm_mem: memory device XML to check
            :param xpath_list: list of expected xpaths
            """
            return all(libvirt_vmxml.check_guest_xml_by_xpaths(
                dimm_mem, xpath, True) for xpath in xpath_list if xpath)

        found_xpath = []
        for memory_device in memory_devices:
            test.log.debug("Current dimm mem device xml is:\n %s", memory_device)
            found = False
            for xpath_list in dimm_xpaths:
                if xpath_list not in found_xpath and _all_xpaths_match(memory_device, xpath_list):
                    found_xpath.append(xpath_list)
                    found = True
                    break
            if not found:
                test.fail("Expected xpath list is %s, but found dimm mem device %s" % (
                    dimm_xpaths, memory_device))

    def check_memory_value(expected):
        """
        Check the domain memory value
        """
        dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if expected != dom_xml.memory:
            test.fail("Expect memory is %s, but found %s" % (expected, dom_xml.memory))

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    init_dimm_dict = params.get("init_dimm_dict")
    plug_dimm_dict = params.get("plug_dimm_dict")
    init_xpath_list = params.get("init_xpath_list")
    plug_xpath_list = params.get("plug_xpath_list")
    vm_attrs = eval(params.get("vm_attrs"))
    mem_value = int(params.get("mem_value"))
    init_size_in_kb = int(params.get("init_size_in_kb", "0"))
    plug_size_in_kb = int(params.get("plug_size_in_kb", "0"))
    with_plug_dimm = params.get("with_plug_dimm", "yes") == "yes"
    with_unplug_dimm = params.get("with_unplug_dimm", "no") == "yes"
    plug_option = params.get("plug_option")
    plug_error = params.get("plug_error")
    unplug_error = params.get("unplug_error")

    try:
        setup_test()

        # Test steps:
        # 1. Define the guest
        # 2. Check the memory device config by virsh dump
        # 3. Cold-plug dimm memory device
        # 4. Check the domain config by virsh dump after cold-plug
        # 5. Unplug dimm memory device
        # 6. Check the domain config by virsh dump after cold-unplug
        test.log.info("TEST_STEP1: Define the guest")
        memory_base.define_guest_with_memory_device(params, init_dimm_dict, vm_attrs)

        test.log.info("TEST_STEP2: Check the memory device config by virsh dump")
        check_dimm_mem_device_xml(init_xpath_list)
        check_memory_value(init_size_in_kb + mem_value)

        if with_plug_dimm:
            test.log.info("TEST_STEP3: Cold-plug dimm memory device")
            cold_plug_dimm = libvirt_vmxml.create_vm_device_by_type('memory', plug_dimm_dict)
            ret = virsh.attach_device(vm.name, cold_plug_dimm.xml, flagstr=plug_option, debug=True)
            libvirt.check_result(ret, plug_error)

        if with_plug_dimm and not plug_error:
            test.log.info("TEST_STEP4: Check the domain config by virsh dump after cold-plug")
            check_dimm_mem_device_xml(init_xpath_list, plug_xpath_list)
            check_memory_value(mem_value + init_size_in_kb + plug_size_in_kb)

        if with_unplug_dimm:
            test.log.info("TEST_STEP5: Unplug dimm memory device")
            unplug_dimm_dict = (
                eval(params.get("unplug_dimm_dict"))
                if params.get("unplug_dimm_dict")
                else init_dimm_dict
            )
            unplug_dimm = libvirt_vmxml.create_vm_device_by_type('memory', unplug_dimm_dict)
            ret = virsh.detach_device(vm.name, unplug_dimm.xml, flagstr=plug_option, debug=True)
            libvirt.check_result(ret, unplug_error)

        if with_unplug_dimm and not unplug_error:
            test.log.info("TEST_STEP6: Check the domain config by virsh dump after cold-unplug")
            check_dimm_mem_device_xml(plug_xpath_list)
            check_memory_value(mem_value + plug_size_in_kb)

    finally:
        bkxml.sync()
