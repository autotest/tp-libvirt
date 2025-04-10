# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.memory import memory_base

virsh_dargs = {'debug': True, 'ignore_status': False}


def get_vm_attrs(test, params):
    """
    Get vm attrs.

    :param test: test object
    :param params: dictionary with the test parameters
    :return vm_attrs: get updated vm attrs dict.
    """
    mem_dict = params.get("mem_dict")
    max_dict = params.get("max_dict")
    numa_dict = params.get("numa_dict")
    all_attrs = ""
    for item in [mem_dict, max_dict, numa_dict]:
        if item != "":
            all_attrs += item + ","

    vm_attrs = eval("{"+all_attrs+"}")
    test.log.debug("Get current vm attrs is :%s", vm_attrs)

    return vm_attrs


def define_guest(params, guest_xml):
    """
    Define guest and check result.

    :param params: dictionary with the test parameters.
    :param guest_xml: the xml you want to define.
    """
    define_error = params.get("define_error")
    device_operation = params.get("device_operation")
    big_size_msg = params.get("big_size_msg")

    res = virsh.define(guest_xml.xml, debug=True)
    if params.get('slot_no_numa'):
        if device_operation == "init_define_with_big_dimm":
            libvirt.check_result(res, big_size_msg)
        else:
            libvirt.check_exit_status(res)
    else:
        libvirt.check_result(res, define_error)


def check_hotplug_result(params, result):
    """
    Check guest hot plug result

    :param params: dict wrapped with params.
    :param result: hot plug result.
    """
    mem_alloc = params.get("memory_allocation")
    device_operation = params.get("device_operation")
    numa_setting = params.get("numa_setting")
    hotplug_error = params.get("hotplug_error")
    hotplug_error_2 = params.get("hotplug_error_2")

    if mem_alloc == "no_maxmemory":
        if numa_setting == "no_numa" and device_operation == "hotplug_with_node":
            libvirt.check_result(result, hotplug_error_2)
    elif mem_alloc == "with_slot" and device_operation == "hotplug_with_node":
        libvirt.check_exit_status(result)
    else:
        libvirt.check_result(result, hotplug_error)


def check_coldplug_result(params, result):
    """
    Check guest cold plug result

    :param params: dict wrapped with params.
    :param result: cold plug result.
    """
    mem_alloc = params.get("memory_allocation")
    device_operation = params.get("device_operation")
    coldplug_error = params.get("coldplug_error")
    coldplug_error_2 = params.get("coldplug_error_2")

    if mem_alloc == "with_slot":
        if device_operation == "coldplug_without_node":
            libvirt.check_result(result, coldplug_error_2)
        else:
            libvirt.check_exit_status(result)
    elif mem_alloc == "no_slot":
        libvirt.check_result(result, coldplug_error)
    elif mem_alloc == "no_maxmemory":
        if device_operation == "coldplug_without_node":
            libvirt.check_result(result, coldplug_error_2)
        else:
            libvirt.check_result(result, coldplug_error)


def run(test, params, env):
    """
    Verify dimm memory device works with various memory allocation
    and guest numa settings.
    """
    def run_test_init_define():
        """
        Test when define guest with dimm memory.
        """
        test.log.info("TEST_STEP1: Define vm with dimm and memory allocation")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.devices = vmxml.devices.append(mem_obj)
        define_guest(params, vmxml)

        if params.get('slot_no_numa') and device_operation == "init_define_with_dimm":
            test.log.info("TEST_STEP2,3: Start vm and check guest xml")
            vm.start()
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            test.log.debug("Get guest xml:%s\n" % vmxml)
            libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, basic_xpath)
            libvirt_vmxml.check_guest_xml_by_xpaths(
                vmxml, eval(dimm_numa_xpath % (str(mem_value-int(target_size)),
                                               target_size)))

    def run_test_coldplug():
        """
        Test guest cold plug with dimm memory.
        """
        test.log.info("TEST_STEP1: Define vm without dimm")
        original_vmxml.setup_attrs(**vm_attrs)
        virsh.define(original_vmxml.xml, **virsh_dargs)

        test.log.info("TEST_STEP2: Cold plug vm with dimm")
        ret = virsh.attach_device(vm_name, mem_obj.xml, debug=True,
                                  flagstr="--config")
        check_coldplug_result(params, ret)

    def run_test_hotplug():
        """
        Test guest hot plug with dimm memory.
        """
        test.log.info("TEST_STEP1: Define vm without dimm")
        original_vmxml.setup_attrs(**vm_attrs)
        virsh.define(original_vmxml.xml, **virsh_dargs)
        virsh.start(vm_name, **virsh_dargs)
        vm.wait_for_login().close()

        test.log.info("TEST_STEP2: Hot plug vm with dimm")
        res = virsh.attach_device(vm_name, mem_obj.xml, debug=True)
        check_hotplug_result(params, res)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    original_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_vmxml.copy()
    vm = env.get_vm(vm_name)
    memory_base.adjust_memory_size(params)

    vm_attrs = get_vm_attrs(test, params)
    device_operation = params.get("device_operation")
    fs = eval(params.get("format_size")) if params.get("format_size") else ""
    dimm_dict = eval(params.get("dimm_dict") % fs)
    mem_obj = libvirt_vmxml.create_vm_device_by_type('memory', dimm_dict)

    target_size = params.get("target_size")
    mem_value = int(params.get("mem_value"))
    basic_xpath = eval(params.get("basic_xpath"))
    dimm_numa_xpath = params.get("dimm_numa_xpath")
    run_test = eval('run_test_%s' % device_operation.split('_with')[0])

    memory_allocation = params.get("memory_allocation")
    numa_setting = params.get("numa_setting")
    params.update(
        {'slot_no_numa': memory_allocation == "with_slot" and numa_setting == "no_numa"})

    try:
        run_test()

    finally:
        teardown_test()
