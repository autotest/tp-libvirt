# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


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
    vm_attrs = ""
    for item in [mem_dict, max_dict, numa_dict]:
        if item != "":
            vm_attrs += item + ","
    vm_attrs = eval("{"+vm_attrs+"}")
    test.log.debug("Get current vm attrs is :%s", vm_attrs)

    return vm_attrs


def define_guest(params, guest_xml):
    """
    Define guest and check result.

    :param params: dictionary with the test parameters.
    :param guest_xml: the xml you want to define.
    """
    memory_allocation = params.get("memory_allocation")
    res = virsh.define(guest_xml.xml, debug=True)
    if libvirt_version.version_compare(9, 6, 0) and params.get('slot_no_numa'):
        libvirt.check_exit_status(res)
    else:
        if memory_allocation == "no_slot" and \
                libvirt_version.version_compare(9, 5, 0):
            define_error = params.get('define_error_2')
        else:
            define_error = params.get("define_error")
        libvirt.check_result(res, define_error)


def check_redefine_result(params, result):
    """
    Check redefine result

    :param params: dict wrapped with params.
    :param result: redefine result.
    """
    memory_allocation = params.get("memory_allocation")
    redefine_error = params.get("redefine_error")
    redefine_error_2 = params.get("redefine_error_2")

    if not libvirt_version.version_compare(9, 6, 0) and memory_allocation == "no_slot":
        libvirt.check_result(result, redefine_error)
    elif libvirt_version.version_compare(9, 5, 0) and \
            not libvirt_version.version_compare(9, 6, 0) and params.get('no_slot_no_numa'):
        libvirt.check_result(result, redefine_error_2)
    elif not libvirt_version.version_compare(9, 6, 0) and params.get('slot_no_numa'):
        libvirt.check_result(result, redefine_error_2)
    else:
        libvirt.check_exit_status(result)


def check_hotplug_result(params, result):
    """
    Check guest hot plug result

    :param params: dict wrapped with params.
    :param result: redefine result.
    """
    mem_alloc = params.get("memory_allocation")
    hotplug_error_2 = params.get("hotplug_error_2")
    hotplug_error_3 = params.get("hotplug_error_3")

    if mem_alloc == "no_maxmemory":
        if libvirt_version.version_compare(9, 5, 0):
            libvirt.check_result(result, hotplug_error_2)
        elif not libvirt_version.version_compare(9, 5, 0):
            libvirt.check_result(result, hotplug_error_3)
    elif mem_alloc == "no_slot":
        libvirt.check_result(result, hotplug_error_2)
    elif mem_alloc == "with_slot":
        libvirt.check_exit_status(result)


def check_coldplug_result(params, result):
    """
    Check guest cold plug result

    :param params: dict wrapped with params.
    :param result: redefine result.
    """
    mem_alloc = params.get("memory_allocation")
    coldplug_error_2 = params.get("coldplug_error_2")
    coldplug_error_3 = params.get("coldplug_error_3")

    if mem_alloc == "no_maxmemory":
        if libvirt_version.version_compare(9, 5, 0):
            libvirt.check_result(result, coldplug_error_2)
        elif not libvirt_version.version_compare(9, 5, 0):
            libvirt.check_result(result, coldplug_error_3)
    elif mem_alloc == "no_slot":
        libvirt.check_result(result, coldplug_error_2)
    elif mem_alloc == "with_slot":
        libvirt.check_exit_status(result)


def run(test, params, env):
    """
    Verify dimm memory device works with various memory allocation
    and guest numa settings.
    """
    def run_test():
        """
        Test dimm memory works with memory allocation and numa.
        """
        test.log.info("TEST_STEP1: Define vm with dimm and memory allocation")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm_attrs = get_vm_attrs(test, params)
        vmxml.setup_attrs(**vm_attrs)

        mem_obj = libvirt_vmxml.create_vm_device_by_type(
            'memory', eval(dimm_dict % target_size))
        vmxml.devices = vmxml.devices.append(mem_obj)
        define_guest(params, vmxml)

        if libvirt_version.version_compare(9, 6, 0) and params.get('slot_no_numa'):
            test.log.info("TEST_STEP2,3: Start vm and check guest xml")
            vm.start()
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            test.log.debug("Get guest xml:%s\n" % vmxml)
            libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, basic_xpath)
            libvirt_vmxml.check_guest_xml_by_xpaths(
                vmxml, eval(dimm_numa_xpath % str(mem_value-int(target_size))))

            test.log.info("TEST_STEP4: Redefine vm with bigger dimm memory")
            vm.destroy()
            mem_obj_bigger = libvirt_vmxml.create_vm_device_by_type(
                'memory', eval(dimm_dict % target_size_big))
            original_vmxml.devices = original_vmxml.devices.append(mem_obj_bigger)
            res = virsh.define(original_vmxml.xml, debug=True)
            libvirt.check_result(res, big_size_msg)

        test.log.info("TEST_STEP5: Redefine vm without any dimm memory")
        vm.destroy()
        original_vmxml2.setup_attrs(**vm_attrs)
        res = virsh.define(original_vmxml2.xml, debug=True)
        check_redefine_result(params, res)

        test.log.info("TEST_STEP6: Start vm")
        vm.start()

        test.log.info("TEST_STEP7: Hot plug a dimm to guest")
        res = virsh.attach_device(vm_name, mem_obj.xml, debug=True)
        libvirt.check_result(res, hotplug_error)

        test.log.info("TEST_STEP8: Hot plug another dimm with node to guest")
        mem_node_obj = libvirt_vmxml.create_vm_device_by_type(
            'memory', eval(dimm_node_dict % target_size))
        res = virsh.attach_device(vm_name, mem_node_obj.xml, debug=True)
        check_hotplug_result(params, res)

        test.log.info("TEST_STEP9: Destroy vm and cold plug a dimm")
        vm.destroy()
        ret = virsh.attach_device(vm_name, mem_obj.xml, debug=True,
                                  flagstr="--config")
        if coldplug_error:
            if coldplug_error not in ret.stderr_text:
                test.fail("Expected get error '%s', but got '%s'" % (
                    coldplug_error, ret))
        else:
            libvirt.check_exit_status(ret)

        test.log.info("TEST_STEP10: Cold plug another dimm with node")
        res = virsh.attach_device(vm_name, mem_node_obj.xml,
                                  debug=True, flagstr="--config")
        check_coldplug_result(params, res)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    original_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    original_vmxml2 = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_vmxml.copy()
    vm = env.get_vm(vm_name)

    dimm_dict = params.get("dimm_dict")
    dimm_node_dict = params.get("dimm_node_dict")
    target_size = params.get("target_size")
    target_size_big = params.get("target_size_big")
    mem_value = int(params.get("mem_value"))
    big_size_msg = params.get("big_size_msg")
    basic_xpath = eval(params.get("basic_xpath"))
    dimm_numa_xpath = params.get("dimm_numa_xpath")

    memory_allocation = params.get("memory_allocation")
    numa_setting = params.get("numa_setting")
    hotplug_error = params.get("hotplug_error")
    coldplug_error = params.get("coldplug_error")
    # scenario A3B1
    params.update(
        {'slot_no_numa': memory_allocation == "with_slot" and numa_setting == "no_numa"})
    # scenario A2B1
    params.update(
        {'no_slot_no_numa': memory_allocation == "no_slot" and numa_setting == "no_numa"})

    try:
        run_test()

    finally:
        teardown_test()
