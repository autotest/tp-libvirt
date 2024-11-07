# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import memory as avocado_mem

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.memory import memory_base


def adjust_virtio_mem_size_unit(params, test):
    """
    Adjust the virtio memory target size and request size to KiB unit.

    :param params: dict, test parameters
    :param test: test object.
    """
    target_size = int(memory_base.convert_data_size(
        params.get("target_size") + params.get("target_size_unit")))
    request_size = int(memory_base.convert_data_size(
        params.get("request_size") + params.get("request_size_unit")))

    params.update({"target_size": target_size})
    params.update({"request_size": request_size})

    test.log.debug("Convert params: target_size to be %s, request_size to be",
                   target_size, request_size)


def check_guest_xml(vm, params, test):
    """
    Check guest xml.

    :param vm: vm object.
    :param params: test parameters object
    :param test: test object.
    """
    expect_mem = int(params.get("mem_value"))
    expect_curr = int(params.get("current_mem"))
    memory_allocation = params.get("memory_allocation")
    numa_topology = params.get("numa_topology")
    no_numa_and_max_mem = \
        memory_allocation == "with_maxmemory" and numa_topology == "without_numa"

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    test.log.debug("Current guest xml is %s\n", vmxml)
    memory = vmxml.get_memory()
    current_mem = vmxml.get_current_mem()
    virtio_mem = vmxml.get_devices("memory")[0]
    target = virtio_mem.target
    target_size = int(target.get_size())
    target_node = target.get_node()
    target_block = target.get_block_size()
    target_request = target.get_requested_size()
    target_current = int(target.get_current_size())

    adjust_virtio_mem_size_unit(params, test)

    test.log.debug("Check guest memory and current memory value")
    if not no_numa_and_max_mem:
        expect_mem = int(params.get("mem_value")) + target_size
        expect_curr = int(params.get("current_mem")) + target_current
    memory_base.compare_values(test, expect_mem, memory, "memory")
    memory_base.compare_values(test, expect_curr, current_mem, "current memory")

    test.log.debug("Check virtio memory size")
    memory_base.compare_values(test, params.get("target_size"),
                               target_size, "virtio mem target size")
    memory_base.compare_values(test, params.get('default_hp'),
                               target_block, "virtio mem block size")
    memory_base.compare_values(test, params.get("request_size"),
                               target_request, "virtio mem request size")
    memory_base.compare_values(test, params.get("request_size"),
                               target_current, "virtio mem current size")
    if params.get("node"):
        memory_base.compare_values(
            test, int(params.get("node")), target_node, "numa node")
    if params.get("numa_mem"):
        memory_base.compare_values(test, params.get("numa_mem"),
                                   vmxml.cpu.numa_cell[0].memory, "numa memory")


def run(test, params, env):
    """
    Verify virtio-mem memory device works with various
    memory allocation and numa setting.
    """
    def run_test_define_guest():
        """
        Define guest with memory allocation and numa, virtio memory.
        """
        test.log.info("TEST_STEP1: Define guest with one virtio-mem device")
        try:
            memory_base.define_guest_with_memory_device(
                params, virtio_mem_dict, vm_attrs)

        except Exception as e:
            if define_error:
                if not re.search(define_error, str(e)):
                    test.fail("Except %s error msg, but got %s" % (define_error, e))
                else:
                    test.log.debug("Define guest with expected error:\n%s", e)
                    return
            else:
                test.fail("Define guest failed")

        test.log.info("TEST_STEP2: Start guest")
        virsh.start(vm_name)
        vm.wait_for_login().close()

        test.log.info("TEST_STEP3: Check guest memory xml")
        check_guest_xml(vm, params, test)

    def run_test_cold_plug():
        """
         Check guest with various memory allocation and numa setting,
        and cold plug virtio-mem memory device successfully.
        """
        test.log.info("TEST_STEP1: Define guest without virtio-mem devices")
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()

        test.log.info("TEST_STEP2: Cold-plug one virtio-mem device")
        memory_base.plug_memory_and_check_result(
            test, params, mem_dict=virtio_mem_dict, operation=operation,
            expected_error=cold_plug_error, flagstr=plug_option)
        if cold_plug_error:
            return

        test.log.info("TEST_STEP3: Start guest and check result")
        res = virsh.start(vm_name)
        libvirt.check_result(res, expected_fails=coldplug_start_error)

    def run_test_hot_plug():
        """
        Check guest with various memory allocation and numa setting,
        and hot plug virtio-mem memory device successfully.
        """
        test.log.info("TEST_STEP1: Define guest without virtio-mem devices")
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()

        test.log.info("TEST_STEP2: Start guest")
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_STEP3: Hot-plug one virtio-mem device")
        memory_base.plug_memory_and_check_result(
            test, params, mem_dict=virtio_mem_dict, operation=operation,
            expected_error=hot_plug_error, flagstr=plug_option)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    memory_base.check_supported_version(params, test, vm)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    default_hugepage_size = avocado_mem.get_huge_page_size()
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    params.update({"default_hp": default_hugepage_size})
    virtio_mem_dict = eval(params.get("virtio_mem_dict") % default_hugepage_size)
    operation = params.get("operation")
    plug_option = params.get("plug_option")
    plug_type = params.get("plug_type")
    define_error = params.get("define_error")
    coldplug_start_error = params.get("coldplug_start_error")
    hot_plug_error = params.get("hot_plug_error")
    cold_plug_error = params.get("cold_plug_error")

    run_test = eval("run_test_%s" % plug_type)

    try:
        run_test()

    finally:
        teardown_test()
