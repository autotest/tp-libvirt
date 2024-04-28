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

from avocado.utils import cpu
from avocado.utils import memory as avocado_mem

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_libvirt import libvirt_memory
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
    max_mem = params.get("max_mem")
    if cpu.get_arch().startswith("aarch"):
        max_mem = params.get("aarch_max_mem")
    vm_attrs = eval(params.get("vm_attrs", "{}") % max_mem)
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


def get_virtio_objs(test, params):
    """
    Get all virtio-mem memory device objects.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :return mem_objs: virtio memory device object list.
    """
    default_pagesize = params.get('default_pagesize')
    mem_objs = []
    for item in [(None, 0),  (default_pagesize, 1)]:
        single_mem = {'mem_model': 'virtio-mem'}
        if item[0] is not None:
            single_mem.update({'source': {'pagesize': item[0]}})
        single_mem.update(
            {'target': {'node': item[1], 'size': int(params.get('target_size')),
                        'requested_size': int(params.get('request_size')),
                        'block_size': int(default_pagesize)}})
        test.log.debug("Get the virtio-mem dict: %s", single_mem)
        mem_obj = libvirt_vmxml.create_vm_device_by_type('memory', single_mem)
        mem_objs.append(mem_obj)
    return mem_objs


def check_qemu_monitor_json(test, params, mem_names, check_item, check_cmd,
                            expected_list):
    """
    Check virsh qemu_monitor_command result.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param mem_names: virtio memory memory name list.
    :param check_item: check item, such as memory backing type or path.
    :param check_cmd: the command to check qemu monitor.
    :param expected_list: expected list.
    """
    vm_name = params.get("main_vm")
    for index, mem_name in enumerate(mem_names):
        res = virsh.qemu_monitor_command(
            vm_name, check_cmd % mem_name, **VIRSH_ARGS).stdout_text

        if 'return' in json.loads(res):
            actual_value = str(json.loads(res)['return'])
            if expected_list[index] in actual_value:
                test.log.debug("Check '%s' is '%s' PASS", check_item, actual_value)
            else:
                test.fail("Expect '%s' for '%s' is '%s', but got '%s'" % (
                    check_item, mem_name, expected_list[index], actual_value))
        else:
            test.fail("Checking '%s' failed" % res)


def check_mb_setting(test, params):
    """
    Check memory backing pre-allocated value, memory backing type and
    memory backing path.

    :param test: test object.
    :param params: dictionary with the test parameters.
    """
    virtio_mem_num = int(params.get("virtio_mem_num"))
    expected_allocated = eval(params.get("expected_allocated", "{}"))
    expected_backing_type = eval(params.get("expected_backing_type", "{}"))
    expected_mem_path = eval(params.get("expected_mem_path", "{}"))
    memory_backing = params.get("memory_backing")
    check_backing_type = params.get("check_backing_type")
    check_mem_path = params.get("check_mem_path")
    allocation_mode = params.get("allocation_mode")
    vm_name = params.get("main_vm")
    file_backend_scenario =\
        memory_backing == "file" or \
        memory_backing == "undefined" and allocation_mode == "set_hugepage"

    mem_name_list = []
    # Check virtio memory pre-allocated value
    ret = virsh.qemu_monitor_command(vm_name, "info memdev", "--hmp",
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

    # Check virtio memory backing type.
    check_qemu_monitor_json(test, params, mem_name_list,
                            'memory backing type', check_backing_type,
                            expected_backing_type)

    # Check virtio memory backing path.
    if file_backend_scenario:
        check_qemu_monitor_json(test, params, mem_name_list,
                                'memory backing path', check_mem_path,
                                expected_mem_path)


def run(test, params, env):
    """
    Verify virtio-mem memory device works with various memory backing type
    """
    def setup_test():
        """
        Allocate huge page memory:
        """
        test.log.info("TEST_SETUP: Set hugepage and add kernel parameter")
        default_pagesize = avocado_mem.get_huge_page_size()
        params.update({'default_pagesize': default_pagesize})
        utils_memory.set_num_huge_pages(int(allocate_huge_pages)/default_pagesize)

    def run_test():
        """
        Test virtio-mem memory device under various memory backing types
        """
        test.log.info("TEST_STEP1: Define vm with virtio-mem")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm_attrs = get_vm_attrs(test, params)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()

        mem_objs = get_virtio_objs(test, params)

        if attach_type == "cold_plug":
            test.log.info("TEST_STEP2: Cold-plug virtio-mem devices")
            for mem in mem_objs:
                virsh.attach_device(vm.name, mem.xml, flagstr="--config",
                                    **VIRSH_ARGS)

        test.log.info("TEST_STEP3: Start guest")
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP4: Restart the service")
        Libvirtd().restart()

        if attach_type == "hot_plug":
            test.log.info("TEST_STEP5: Hot-plug virtio-mem devices")
            for mem in mem_objs:
                virsh.attach_device(vm.name, mem.xml,
                                    wait_for_event=True, **VIRSH_ARGS)
        test.log.info("TEST_STEP 6-9:Check virtio memory backend pre-allocated "
                      "value, memory backing type and memory backing path.")
        check_mb_setting(test, params)

        test.log.info("TEST_STEP10: Consume guest memory successfully")
        session = vm.wait_for_login()
        status, output = libvirt_memory.consume_vm_freememory(
            session, consume_value=consume_value)
        if status:
            test.fail("Fail to consume guest memory. Error:%s" % output)
        session.close()

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
    memory_base.adjust_memory_size(params)

    allocate_huge_pages = re.findall(r'\d+', params.get("allocate_huge_pages"))[0]
    attach_type = params.get("attach_type")
    consume_value = int(params.get("consume_value"))

    try:
        memory_base.check_supported_version(params, test, vm)
        setup_test()
        run_test()

    finally:
        teardown_test()
