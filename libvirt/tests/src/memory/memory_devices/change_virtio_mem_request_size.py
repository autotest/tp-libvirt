# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import memory

from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml
from virttest.staging import utils_memory

from provider.memory import memory_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


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


def define_guest_with_basic_virtio_mem(test, params, vm_name, mem_basic_dict):
    """
    Define guest with virtio memory device.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param vm_name: vm name.
    :param mem_basic_dict: the virtio memory device dict.
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vm_attrs = get_vm_attrs(test, params)
    vmxml.setup_attrs(**vm_attrs)

    libvirt_vmxml.modify_vm_device(
        vmxml, "memory",
        dev_dict=eval(mem_basic_dict % (params.get('target_size'),
                                        params.get('request_size'),
                                        params.get('default_pagesize'))))
    test.log.debug("Define vm with %s.", vm_xml.VMXML.new_from_inactive_dumpxml(
        vm_name))


def get_various_size(test, vm_name, check_item='requested_size', index=0):
    """
    Get various memory size.

    :param test: test object.
    :param vm_name: vm name.
    :param check_item: The item you want to check, eg: requested_size.
    :param index: memory index of memory list in devices.
    :return actual_size, the actual size in xml.
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

    if int(actual_size) != int(expected_size):
        test.fail("Expect to get '%s':'%s', but got:'%s' in '%sth' "
                  "memory" % (check_item, str(expected_size), actual_size, index+1))
    else:
        test.log.debug("Check '%s' is '%s' successfully", (check_item, expected_size))


def update_virtio_mem_request_size(test, params, vm_name, device_opt, target_requested):
    """
    Update specified virtio memory request value.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param vm_name: vm name.
    :param device_opt: the device option to update.
    :param target_requested: the request value to update.
    """
    error_msg = params.get("error_msg")
    expected_event = params.get("expected_event")
    virsh_opts = params.get("virsh_opts")
    guest_state = params.get("guest_state")

    event_session = virsh.EventTracker.start_get_event(vm_name)
    res = virsh.update_memory_device(
        vm_name, options=virsh_opts % (device_opt, target_requested), debug=True)
    libvirt.check_result(res, error_msg)

    event_output = virsh.EventTracker.finish_get_event(event_session)
    pattern = r"%s\S+ \S+ \S+ '\S+\s+\S+ virtiomem%s" % (
        expected_event, re.findall(r"\d+", device_opt)[0])

    def _check_event_output(patt, output, expect_exist=True):
        is_existed = bool(re.findall(patt, output))
        if is_existed != expect_exist:
            test.fail('Expect %s to get: %s from the event output:%s' % (
                '' if expect_exist else 'not', patt, output))
        else:
            test.log.debug("Check event %s success in the event output", patt)

    if guest_state != "shutoff_guest":
        _check_event_output(pattern, event_output, expect_exist=error_msg is None)


def check_mem_total(test, params, session, old_mem_total):
    """
    Compare mem total value in different scenarios.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param session: vm session.
    :param old_mem_total: The old mem total value to compare.
    :return: new_mem_total, current mem total value.
    """

    update_req = int(memory_base.convert_data_size(
        params.get("update_request_size"), 'KiB'))
    basic_req = int(memory_base.convert_data_size(
        params.get("basic_request"), 'KiB'))
    new_mem_total = int(utils_memory.memtotal(session))
    if params.get('normal_or_zero_request'):
        if new_mem_total - old_mem_total != update_req - basic_req:
            test.fail("Expect two memTotal values(%s and %s) difference is "
                      "(%s - %s)" % (new_mem_total, old_mem_total,
                                     update_req, basic_req))

    elif params.get('bigger_or_not_muti_request'):
        if new_mem_total != old_mem_total:
            test.fail("Expect mem total to be '%s', but found '%s'" % (
                old_mem_total, new_mem_total))
    test.log.debug("Check memtotal success")

    return new_mem_total


def check_guest_xml(test, vm_name, params):
    """
    Check correct memory size in guest xml.

    :param test: test object.
    :param vm_name: vm name
    :param params: dictionary with the test parameters.
    """
    expect_xpath = params.get("expect_xpath")
    target_size = int(params.get("target_size"))
    mem_value = int(params.get("mem_value"))
    current_mem = int(params.get("current_mem"))
    basic_request = int(re.findall(r'\d+', params.get("basic_request"))[0])
    update_request_size = int(memory_base.convert_data_size(
        params.get("update_request_size"), 'KiB'))

    test.log.debug("Start checking memory size and current memory size")
    virtio_mem_value = current_0 = current_1 = 0
    if params.get('normal_or_zero_request'):
        virtio_mem_value = current_0 = current_1 = update_request_size
    elif params.get('bigger_or_not_muti_request'):
        virtio_mem_value = current_0 = current_1 = basic_request
    xpath = expect_xpath % (mem_value + target_size * 2,
                            current_mem + current_0 + current_1)
    test.log.debug("Checking xml pathern is :%s", xpath)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    test.log.debug("Current mem xml is :%s\n", vmxml)
    libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, eval(xpath))

    test.log.debug("Start checking all memory device size")
    check_list = [(params.get('default_pagesize'), 'block_size', 0),
                  (params.get('default_pagesize'), 'block_size', 1),
                  (virtio_mem_value, 'requested_size', 0),
                  (virtio_mem_value, 'requested_size', 1),
                  (virtio_mem_value, 'current_size', 0),
                  (virtio_mem_value, 'current_size', 1)]
    for items in check_list:
        check_various_size(test, vm_name, items[0], check_item=items[1],
                           index=items[2])


def update_and_check_memory_size(test, params, session, update_device,
                                 update_size, old_mem_total):
    """
    Update request size and check memory size.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param session: vm session
    :param update_device: the device to update
    :param update_size: the size to update
    :param old_mem_total: the old mem total value to compare with new mem total.
    :return new_mem_total: return the current mem total
    """
    vm_name = params.get("main_vm")
    update_virtio_mem_request_size(test, params, vm_name, update_device,
                                   update_size)
    new_mem_total = check_mem_total(test, params, session, old_mem_total)
    return new_mem_total


def check_delayed_current(test, params, mem_index, expected_current_size):
    """
    Check the virtio memory current value.
    :param test: test object.
    :param params: dictionary with the test parameters.
    :param expected_current_size, expected current size value.
    :param mem_index, Check after or before attaching memory device.
    """
    vm_name = params.get("main_vm")

    def _get_virtio_current():
        return vm_xml.VMXML.new_from_dumpxml(
            vm_name).devices.by_device_tag("memory")[
            mem_index].target.current_size

    if not utils_misc.wait_for(
            lambda: expected_current_size == _get_virtio_current(), 20):
        test.fail(
            "Attached virtio memory current size should be %s" % expected_current_size)
    test.log.debug("Checked attached virtio memory current size successfully")


def run(test, params, env):
    """
    Verify virtio-mem memory device requested memory size could be changed
    """
    def setup_test():
        """
        Allocate huge page memory and update kernel parameter.
        """
        test.log.info("TEST_SETUP: Set hugepage and add kernel parameter")
        default_pagesize = memory.get_huge_page_size()
        params.update({'default_pagesize': default_pagesize})
        utils_memory.set_num_huge_pages(int(allocate_huge_pages)/default_pagesize)

    def run_test_shutoff_guest():
        """
        1.Define vm.
        2.Change virtio-mem requested size and check virtio-mem config
        """
        test.log.info("TEST_STEP1: Define vm with numa and virtio-mem device")
        if vm.is_alive():
            virsh.destroy(vm_name, **VIRSH_ARGS)
        define_guest_with_basic_virtio_mem(test, params, vm_name, mem_basic)

        test.log.info("TEST_STEP2: Change the requested size of the virtio-mem")
        update_virtio_mem_request_size(test, params, vm_name,
                                       basic_device, update_request_size)
        if params.get('bigger_or_not_muti_request'):
            return

        test.log.info("TEST_STEP3: Check the virtio-mem memory device config")
        converted_req = int(memory_base.convert_data_size(update_request_size, requested_unit))
        check_list = [(target_size, 'size'), (converted_req, 'requested_size'),
                      (basic_node, 'node'), (params.get('default_pagesize'), 'block_size')]
        for items in check_list:
            check_various_size(test, vm_name, items[0], check_item=items[1])

    def run_test_running_guest():
        """
        1.Define vm.
        2.Change virtio-mem requested size and check virtio-mem config
        3.Hotplug a virtio-mem device and check virtio-mem config
        """
        test.log.info("TEST_STEP1: Define vm with numa and virtio-mem device")
        define_guest_with_basic_virtio_mem(test, params, vm_name, mem_basic)

        test.log.info("TEST_STEP2: Start guest")
        virsh.start(vm_name, **VIRSH_ARGS)
        session = vm.wait_for_login()

        test.log.info("TEST_STEP3: Attach a new virtio-memory device")
        mem_obj = libvirt_vmxml.create_vm_device_by_type(
            "memory", eval(mem_attach % (params.get('target_size'),
                                         params.get('request_size'),
                                         params.get('default_pagesize'))))
        virsh.attach_device(vm.name, mem_obj.xml, wait_for_event=True, **VIRSH_ARGS)
        check_delayed_current(test, params, 1, int(params.get('request_size')))

        test.log.info("TEST_STEP4: Get the first time total memory in guest")
        mem_total_1 = int(utils_memory.memtotal(session))

        test.log.info("TEST_STEP5,6: Change the first virtio-mem requested size"
                      "with alias name and check total memory")
        mem_total_2 = update_and_check_memory_size(
            test, params, session, basic_device_alias, update_request_size,
            mem_total_1)

        test.log.info("TEST_STEP7,8: Change the second virtio-mem requested "
                      "size with node id and check total memory")
        update_and_check_memory_size(test, params, session, attached_device,
                                     update_request_size, mem_total_2)
        session.close()

        test.log.info("TEST_STEP9: Check the memory allocation and virtio-mem "
                      "device active config by virsh dumpxml")
        check_guest_xml(test, vm.name, params)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        utils_memory.set_num_huge_pages(0)
        bkxml.sync()

    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    allocate_huge_pages = re.findall(r'\d+', params.get("allocate_huge_pages"))[0]
    guest_state = params.get("guest_state")
    basic_node = params.get("basic_node")
    target_size = int(params.get("target_size"))
    mem_basic = params.get("mem_basic", "{}")
    mem_attach = params.get("mem_attach", "{}")
    attached_device = params.get("attached_device")
    basic_device = params.get("basic_device")
    basic_device_alias = params.get("basic_device_alias")
    update_request_size = params.get("update_request_size")
    requested_unit = params.get("requested_unit")
    requested_setting = params.get("requested_setting")
    params.update(
        {'normal_or_zero_request': requested_setting in [
            "normal_requested", "zero_requested"]})
    params.update(
        {'bigger_or_not_muti_request': requested_setting in [
            "bigger_requested", "not_mutiple_of_block_requested"]})

    run_test = eval('run_test_%s' % guest_state)
    memory_base.check_supported_version(params, test, vm)
    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
