# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml.devices import memory
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml
from virttest.staging import utils_memory

from provider.memory import memory_base


def get_vm_attrs(test, params):
    """
    Get vm attrs.

    :param test: test object
    :param params: dictionary with the test parameters
    :return vm_attrs: get updated vm attrs dict.
    """
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    source_attr = params.get("source_attr", "")
    hugepages_attr = params.get("hugepages_attr", "")
    mb_value = ""

    for item in [source_attr, hugepages_attr]:
        if item != "":
            mb_value += item + ","
    mb_attrs = eval("{'mb':{%s}}" % mb_value[:-1])

    vm_attrs.update(mb_attrs)
    test.log.debug("Get current vm attrs is :%s", vm_attrs)

    return vm_attrs


def get_various_size(test, vm_name, check_item='requested_size', index=0):
    """
    Get various memory size.

    :param test: test object.
    :param vm_name: vm name.
    :param check_item: The item you want to check, eg: requested_size.
    :param index: memory index of memory list in devices.
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    mem_list = vmxml.devices.by_device_tag('memory')
    actual_attrs = mem_list[index].fetch_attrs()
    test.log.debug("Get memory attrs list is: '%s'", actual_attrs)
    actual_size = actual_attrs['target'][check_item]

    return actual_size


def check_various_size(test, vm_name, expected_size,
                       check_item='requested_size', index=0):
    """
    Check Various size in vmxml memory device

    :param test: test object.
    :param vm_name: vm name.
    :param expected_size: expected size value.
    :param check_item: The item you want to check, eg: requested_size.
    :param index: memory index of memory list in devices.
    """
    actual_size = get_various_size(test, vm_name, check_item=check_item,
                                   index=index)

    if str(actual_size) != str(expected_size):
        test.fail("Expect to get '%s':'%s', but got:'%s' in '%sth' memory"
                  "" % (check_item, str(expected_size), actual_size, index+1))
    else:
        test.log.debug("Check '%s' is '%s' correctly", check_item, expected_size)


def check_size_changing(test, params, vm, device, size, operation='increase'):
    """
    Check size increasing or decreasing

    :params: test: test object
    :params: params: system parameter
    :params: vm: vm object
    :params: device: the device needs to update
    :params: size: the size needs to update
    :params: operation: update memory operation, increase or decrease.
    """

    virsh_opts = params.get("virsh_opts")
    expected_event = params.get("attached_event")
    VIRSH_ARGS = {'debug': True, 'ignore_status': False}

    session = vm.wait_for_login()
    mem_total_old = int(utils_memory.memtotal(session))

    event_session = virsh.EventTracker.start_get_event(vm.name)
    virsh.update_memory_device(
        vm.name, options=virsh_opts % (device, size), **VIRSH_ARGS)
    event_output = virsh.EventTracker.finish_get_event(event_session)
    if not re.search(expected_event, event_output):
        test.fail('Not find: %s from event output:%s' % (
            expected_event, event_output))

    mem_total_new = int(utils_memory.memtotal(session))
    if operation == "decrease":
        size = -(int(size))
    if mem_total_new - mem_total_old != int(size):
        test.fail("Expect the difference of '%s' and '%s' is '%s'" %
                  (mem_total_new, mem_total_old, size))
    session.close()


def check_guest_xml(test, vm_name, params):
    """
    Check correct memory size in guest xml.

    :param test: test object.
    :param vm_name: vm name
    :param params: dictionary with the test parameters.
    """
    expect_xpath = params.get("expect_xpath")
    target_size = params.get("target_size")
    basic_bigger = params.get("basic_bigger")
    attach_smaller = params.get("attach_smaller")
    mem_value = int(params.get("mem_value"))
    current_mem = int(params.get("current_mem"))

    test.log.debug("Start checking memory size and current memory size")
    current_0 = get_various_size(test, vm_name, check_item='current_size', index=0)
    current_1 = get_various_size(test, vm_name, check_item='current_size', index=1)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    xpath = expect_xpath % (mem_value + int(target_size) * 2,
                            current_mem + current_0 + current_1)
    test.log.debug("Checking xml pathern is :%s", xpath)
    libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, eval(xpath))

    test.log.debug("Start checking all memory device size")
    check_list = [(target_size, 'size', 0), (target_size, 'size', 1),
                  (basic_bigger, 'requested_size', 0),
                  (attach_smaller, 'requested_size', 1),
                  (basic_bigger, 'current_size', 0),
                  (attach_smaller, 'current_size', 1)]
    for items in check_list:
        check_various_size(test, vm_name, items[0], check_item=items[1],
                           index=items[2])


def run(test, params, env):
    """
    Verify virtio-mem memory device requested memory size could be changed
    """
    def setup_test():
        """
        Allocate huge page memory
        """
        utils_memory.set_num_huge_pages(int(nr_hugepages))

    def run_test():
        """
        1.Define vm.
        2.Change virtio-mem requested size and check virtio-mem config
        3.Hotplug a virtio-mem device and check virtio-mem config
        """
        test.log.info("TEST_STEP1: Define vm and check result")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm_attrs = get_vm_attrs(test, params)
        vmxml.setup_attrs(**vm_attrs)
        test.log.debug("Define vm with %s.", vmxml)
        virsh.define(vmxml.xml, debug=True, ignore_status=False)
        libvirt_vmxml.modify_vm_device(vmxml, 'memory', mem_basic)

        test.log.info("TEST_STEP2: Change the requested size of the virtio-mem")
        virsh.update_memory_device(
            vm_name, options=virsh_opts % (default_device, set_size),
            **VIRSH_ARGS)

        test.log.info("TEST_STEP3: Check the virtio-mem memory device config")
        converted_size = memory_base.convert_data_size(set_size, requested_unit)
        check_various_size(test, vm_name, int(converted_size))

        test.log.info("TEST_STEP4: Change request size bigger than last size")
        result = virsh.update_memory_device(
            vm_name, options=virsh_opts % (default_device, bigger_size), debug=True)
        libvirt.check_result(result, bigger_size_error)

        test.log.info("TEST_STEP5: Change request size to zero")
        virsh.update_memory_device(
            vm_name, options=virsh_opts % (default_device, zero_size), **VIRSH_ARGS)

        test.log.info("TEST_STEP6: Check the virtio-mem memory device config")
        check_various_size(test, vm_name, zero_size)

        test.log.info("TEST_STEP7: Start guest")
        vm.start()

        test.log.info("TEST_STEP8: Check the memory allocated")
        session = vm.wait_for_login()
        mem_total_1 = int(utils_memory.memtotal(session))

        test.log.info("TEST_STEP9: Hotplug a virtio-mem device ")
        mem_obj = memory.Memory()
        mem_obj.setup_attrs(**mem_attach)
        virsh.attach_device(vm.name, mem_obj.xml, wait_for_event=True,
                            ignore_status=False, debug=True)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
        test.log.debug("After attached, Get vm xml is '%s'", vmxml)

        test.log.info("TEST_STEP10: Check total memory increased")
        if utils_misc.wait_for(
                lambda: attach_request_size != int(
                    utils_memory.memtotal(session)) - mem_total_1, 30, 5):
            test.fail("Expect the difference of '%s' and '%s' is '%s'" %
                      (int(utils_memory.memtotal(session)), mem_total_1,
                       attach_request_size))
        session.close()

        test.log.info("TEST_STEP11-12: Increase the virtio-mem and Check "
                      "total memory increased")
        check_size_changing(test, params, vm, basic_device_alias,
                            basic_bigger)

        test.log.info("TEST_STEP13-14: Decrease the virtio-mem and Check "
                      "total memory decrease")
        check_size_changing(test, params, vm, attached_device, attach_smaller,
                            operation='decrease')

        test.log.info("TEST_STEP 15: Check guest xml")
        check_guest_xml(test, vm.name, params)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        utils_memory.set_num_huge_pages(0)

    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    mem_basic = eval(params.get("mem_basic", "{}"))
    mem_attach = eval(params.get("mem_attach", "{}"))
    default_device = params.get("default_device")
    nr_hugepages = params.get("nr_hugepages")
    virsh_opts = params.get("virsh_opts")
    bigger_size = params.get("bigger_size")
    attach_request_size = int(params.get("attach_request_size", 0))
    set_size = params.get("set_size")
    requested_unit = params.get("requested_unit")
    bigger_size_error = params.get("bigger_size_error")
    zero_size = params.get("zero_size")
    basic_device_alias = params.get("basic_device_alias")
    attached_device = params.get("attached_device")
    basic_bigger = params.get("basic_bigger")
    attach_smaller = params.get("attach_smaller")

    vm = env.get_vm(vm_name)
    VIRSH_ARGS = {'debug': True, 'ignore_status': False}

    try:
        memory_base.check_supported_version(params, test, vm)
        setup_test()
        run_test()

    finally:
        teardown_test()
