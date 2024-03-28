# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re
import json

from virttest import virsh
from virttest.libvirt_xml.devices import memory
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_libvirt import libvirt_memory
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
    alloc_attr = params.get("alloc_attr", "")
    hugepages_attr = params.get("hugepages_attr", "")

    mb_value = ""
    for item in [source_attr, alloc_attr, hugepages_attr]:
        if item != "":
            mb_value += item + ","
    mb_attrs = eval("{'mb':{%s}}" % mb_value[:-1])

    vm_attrs.update(mb_attrs)
    test.log.debug("Get current vm attrs is :%s", vm_attrs)

    return vm_attrs


def get_virtio_mem(test, params):
    """
    Get 4 basic different virtio-mem memory devices.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :return mem_list: virtio-mem attr dict list.
    """
    source_pagesize = int(params.get('source_pagesize'))
    mem_list = []
    for item in [(None, 0), (source_pagesize, 0),
                 (None, 1), (source_pagesize, 1)]:
        single_mem = eval(params.get("mem_basic"))

        target = single_mem['target']
        target.update({'node': item[1]})

        if item[0] is not None:
            single_mem.update({'source': {'pagesize': item[0]}})

        mem_list.append(single_mem)

    test.log.debug("Get all virtio-mem list:'%s'", mem_list)
    return mem_list


def check_qemu_monitor_json(test, vm, params, mem_names, check_item, check_cmd,
                            expected_list):
    """
    Check virsh qemu_monitor_command result.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param vm: vm object.
    :param mem_names: virtio memory memory name list.
    :param check_item: check item, such as memory backing type or path.
    :param check_cmd: the command to check qemu monitor.
    :param expected_list: expected list.
    """
    for index, mem_name in enumerate(mem_names):
        res = virsh.qemu_monitor_command(
            vm.name, check_cmd % mem_name, debug=True).stdout_text

        if 'return' in json.loads(res):
            actual_value = str(json.loads(res)['return'])
            if expected_list[index] in actual_value:
                test.log.debug("Check '%s' is '%s' PASS", check_item, actual_value)
            else:
                test.fail("Expect '%s' for '%s' is '%s', but got '%s'" % (
                    check_item, mem_name, expected_list[index], actual_value))
        else:
            error_msg = params.get('error_msg')
            actual_error = json.loads(res)['error']['desc']
            if not re.search(error_msg, actual_error):
                test.fail("Expected to get '%s' in '%s'" % (error_msg,
                                                            actual_error))
            else:
                test.log.debug("Check '%s' PASS ", actual_error)


def check_mb_setting(test, params, vm):
    """
    Check memory backing pre-allocated value, memory backing type and
    memory backing path.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param vm: vm object.
    """
    virtio_mem_num = int(params.get("virtio_mem_num"))
    expected_allocated = eval(params.get("expected_allocated", "{}"))
    expected_backing_type = eval(params.get("expected_backing_type", "{}"))
    expected_mem_path = eval(params.get("expected_mem_path", "{}"))

    check_backing_type = params.get("check_backing_type")
    check_mem_path = params.get("check_mem_path")
    mem_name_list = []

    # Check pre-allocated value
    ret = virsh.qemu_monitor_command(vm.name, "info memdev", "--hmp",
                                     debug=True).stdout_text.replace("\r\n", "")
    for index in range(virtio_mem_num):
        mem_name = "memvirtiomem%d" % index
        pattern = "memory backend: %s.*prealloc: %s " % (
            mem_name, expected_allocated[index])

        if not re.search(pattern, ret):
            test.fail("Expect '%s' exist, but not found" % pattern)
        else:
            test.log.debug("Check access pre-allocated value is '%s': PASS", pattern)
        mem_name_list.append(mem_name)

    # Check memory backing type.
    check_qemu_monitor_json(test, vm, params, mem_name_list,
                            'memory backing type', check_backing_type, expected_backing_type)
    # Check memory backing path.
    check_qemu_monitor_json(test, vm, params, mem_name_list,
                            'memory backing path', check_mem_path, expected_mem_path)


def run(test, params, env):
    """
    Verify virtio-mem memory device works with various memory backing type
    """
    def setup_test():
        """
        Allocate huge page memory:
        """
        utils_memory.set_num_huge_pages(page_num)

    def run_test():
        """
        1.Define vm with 4 basic virtio-mem devices.
        2.Check if virtio-mem memory device is pre-allocated, backing type and path.
        3.Consume guest memory.
        4.Define vm without 4 basic virtio-mem devices.
        5. Attach 4 virtio-mem and hotplug, check same as step2
        """
        test.log.info("TEST_STEP1: Define vm with virtio-mem")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm_attrs = get_vm_attrs(test, params)
        vmxml.setup_attrs(**vm_attrs)

        virtio_mems = get_virtio_mem(test, params)
        for mem in virtio_mems:
            virtio_mem = memory.Memory()
            virtio_mem.setup_attrs(**mem)
            vmxml.devices = vmxml.devices.append(virtio_mem)
        vmxml.sync()

        test.log.info("TEST_STEP2: Start guest")
        vm.start()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Got init guest xml:\n %s", vmxml)

        test.log.info("TEST_STEP3,4,5:Check memory backing pre-allocated value,"
                      "memory backing type and memory backing path.")
        check_mb_setting(test, params, vm)

        test.log.info("TEST_STEP6: Consume guest memory successfully")
        session = vm.wait_for_login()
        status, output = libvirt_memory.consume_vm_freememory(session)
        if status:
            test.fail("Fail to consume guest memory. Error:%s" % output)
        session.close()

        test.log.info("TEST_STEP7: Destroy vm")
        vm.destroy()

        test.log.info("TEST_STEP8: Define guest without virtio-mem memory")
        original_xml.setup_attrs(**vm_attrs)
        original_xml.sync()
        vm.start()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Redefine guest xml without virtio-mem by:\n %s", vmxml)

        test.log.info("TEST_STEP9: Restart service")
        Libvirtd().restart()

        test.log.info("TEST_STEP10: Hot plug all memory device")
        for mem in virtio_mems:
            virtio_mem = memory.Memory()
            virtio_mem.setup_attrs(**mem)
            virsh.attach_device(vm_name, virtio_mem.xml,
                                debug=True, ignore_status=False)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("After attaching all virtio-mem, the vm xml is:\n %s", vmxml)

        test.log.info("TEST_STEP11:Check memory backing pre-allocated value,"
                      "memory backing type and memory backing path.")
        check_mb_setting(test, params, vm)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        utils_memory.set_num_huge_pages(0)
        bkxml.sync()

    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_xml.copy()
    vm = env.get_vm(vm_name)
    page_num = int(params.get("page_num"))

    try:
        memory_base.check_supported_version(params, test, vm)
        setup_test()
        run_test()

    finally:
        teardown_test()
