# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os

from avocado.utils import process
from avocado.utils import memory

from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory
from virttest.utils_test import libvirt

from provider.memory import memory_base

virsh_dargs = {"ignore_status": False, "debug": True}


def adjust_virtio_dict(params):
    """
    Adjust virtio memory dict and plugging dict.

    :param params: dictionary with the test parameters.
    """

    default_page_size = utils_memory.getpagesize()
    default_hugepage_size = memory.get_huge_page_size()

    case = params.get("case")
    source_dict = params.get("source_dict", "")
    addr_dict = params.get("addr_dict", "")
    plug_addr_dict = params.get("plug_addr_dict", "")
    virtio_dict = eval(params.get("virtio_dict", "{}") % default_hugepage_size)
    plug_dict = eval(params.get("plug_dict") % default_hugepage_size)

    if source_dict:
        if case == "source_mib_and_hugepages":
            virtio_dict['source'] = eval(source_dict % default_page_size)
        else:
            virtio_dict['source'] = eval(source_dict % default_hugepage_size)
        plug_dict['source'] = eval(source_dict % default_hugepage_size)
    if addr_dict:
        target = virtio_dict["target"]
        target['address'] = eval(addr_dict)

        plug_target = plug_dict['target']
        plug_target['address'] = eval(plug_addr_dict)

    return virtio_dict, plug_dict


def adjust_virtio_size(params):
    """
    Adjust all virtio related size to KiB.

    :param params: dict wrapped with params.
    """
    plug_target_size = int(params.get('plug_target_size', 0))
    plug_request_size = int(params.get('plug_request_size', 0))
    target_size, request_size = int(params.get('target_size')), int(
        params.get('request_size'))
    plug_size_unit = params.get('plug_size_unit')
    plug_request_unit = params.get('plug_request_unit')
    size_unit, request_unit = params.get('size_unit'), params.get(
        'request_unit')

    def _convert_size(curr_size, curr_unit):
        if curr_unit != "KiB":
            new_size = memory_base.convert_data_size(str(curr_size) + curr_unit)
            return int(new_size)
        else:
            return int(curr_size)

    target_size = _convert_size(target_size, size_unit)
    request_size = _convert_size(request_size, request_unit)
    plug_target_size = _convert_size(plug_target_size, plug_size_unit)
    plug_request_size = _convert_size(plug_request_size, plug_request_unit)

    return target_size, request_size, plug_target_size, plug_request_size


def compare_two_values(test, expected, actual, item_name=''):
    """
    Compare two value should be equal

    :param test: test object
    :param expected, expected value
    :param actual, actual value
    :param item_name,checking item name, such as memory value, current memory
    """
    if actual != expected:
        test.fail(
            "Expect %s is %s , but got %s" % (item_name, expected, actual))
    else:
        test.log.debug("Checked the %s successfully", item_name)


def check_source_and_addr_xml(test, params, virtio_mem_xml, mem_index=0):
    """
    Check virtio memory source and address xml if existed.

    :param test: test object
    :param params: dictionary with the test parameters
    :param virtio_mem_xml, virtio memory xml
    :param mem_index, Check after or before attaching memory device.
    """
    addr_dict = params.get("addr_dict", "")
    source_dict = params.get("source_dict", "")
    expected_base = params.get("plug_base") if mem_index == 1 \
        else params.get("base")
    expected_source_pgsize = memory.get_huge_page_size() if mem_index == 1 \
        else utils_memory.getpagesize()

    if source_dict:
        if virtio_mem_xml.source.pagesize != expected_source_pgsize:
            test.fail("Got virtio memory source pagesize %s, should be %s" % (
                virtio_mem_xml.source.pagesize, expected_source_pgsize))
    if addr_dict:
        actual_base = virtio_mem_xml.target.address.attrs.get("base")
        if actual_base != expected_base:
            test.fail("Got virtio memory address base %s, should be %s" % (
                actual_base, expected_base))


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

    params.update({"second_virtio_curr": _get_virtio_current()})
    params.update({"acutal_curr": vm_xml.VMXML.new_from_dumpxml(vm_name).current_mem})


def check_guest_xml(test, params, mem_index=0):
    """
    Check guest xml.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param mem_index, the memory order in the guest, set 1 when checking the
    plugged virtio memory.
    """
    vm_name = params.get("main_vm")
    mem_value = int(params.get("mem_value"))
    current_mem = int(params.get("current_mem"))

    target_size, request_size, plug_target_size, plug_request_size = \
        adjust_virtio_size(params)
    default_hugepage_size = memory.get_huge_page_size()

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    virtio_mem_xml = vmxml.devices.by_device_tag("memory")[mem_index]
    acutal_mem = vmxml.memory
    acutal_curr = vmxml.current_mem
    acutal_virtio_target = virtio_mem_xml.target.size
    acutal_virtio_block = virtio_mem_xml.target.block_size
    acutal_virtio_requested = virtio_mem_xml.target.requested_size
    acutal_virtio_current = virtio_mem_xml.target.current_size

    check_source_and_addr_xml(test, params, virtio_mem_xml, mem_index)
    if mem_index == 0:
        params.update({"first_virtio_curr": acutal_virtio_current})
        params.update({"expected_mem0": mem_value + target_size})
        params.update({"expected_curr0": current_mem + acutal_virtio_current})

        compare_two_values(
            test, params.get("expected_mem0"), acutal_mem, 'memory')
        compare_two_values(
            test, params.get("expected_curr0"), acutal_curr, 'current memory')
        compare_two_values(
            test, target_size, acutal_virtio_target, 'virtio memory target size')
        compare_two_values(
            test, default_hugepage_size, acutal_virtio_block, 'virtio memory block size')
        compare_two_values(
            test, request_size, acutal_virtio_requested, 'virtio memory requested size')
        compare_two_values(
            test, acutal_virtio_current, request_size, 'virtio memory current size')

    elif mem_index == 1:
        compare_two_values(
            test, plug_target_size, acutal_virtio_target,
            'Attached virtio memory target size')
        compare_two_values(
            test, default_hugepage_size, acutal_virtio_block,
            'Attached virtio memory block size')
        compare_two_values(
            test, plug_request_size, acutal_virtio_requested,
            'Attached virtio memory requested size')

        check_delayed_current(test, params, mem_index, plug_request_size)

        curr1, curr2 = params.get("first_virtio_curr"), params.get("second_virtio_curr")
        params.update(
            {"expected_mem1": mem_value + target_size + plug_target_size})
        params.update({"expected_curr1": current_mem + curr1 + curr2})
        compare_two_values(
            test, params.get("expected_mem1"), acutal_mem, 'memory')
        compare_two_values(
            test, params.get("expected_curr1"), params.get("acutal_curr"),
            'current memory')


def check_guest_virsh_dominfo(vm, test, params, hot_plugged=False):
    """
    Check memory value and current memory value in virsh dominfo result.

    :param vm: vm object.
    :param test: test object.
    :param params: dictionary with the test parameters.
    :param hot_plugged: boolean, the flag of hotplugging.
    """
    if hot_plugged:
        expected_mem = params.get("expected_mem1")
        expected_curr = params.get("expected_curr1")
    else:
        expected_mem = params.get("expected_mem0")
        expected_curr = params.get("expected_curr0")

    memory_base.check_dominfo(vm, test, str(expected_mem), str(expected_curr))


def check_after_attach(vm, test, params):
    """
    Check the below points after plugging.

    1. Check the audit log by ausearch.
    2. Check the libvirtd log.
    3. Check the memory allocation and memory device config.
    4. Check the memory info by virsh dominfo.
    5. Check the guest memory.
    :param vm: vm object.
    :param test: test object.
    :param params: dictionary with the test parameters.
    :param operation: string, the flag for attaching or detaching.
    """
    mem_value = int(params.get("mem_value"))
    expected_log = params.get("expected_log")
    audit_cmd = params.get("audit_cmd")
    target_size, request_size, plug_target_size, plug_request_size = \
        adjust_virtio_size(params)

    libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
    ausearch_check = params.get("ausearch_check") % (
        mem_value + target_size, mem_value + target_size + plug_target_size)

    # Check the audit log by ausearch.
    ausearch_result = process.run(audit_cmd, shell=True)
    libvirt.check_result(ausearch_result, expected_match=ausearch_check)
    test.log.debug("Check audit log %s successfully." % ausearch_check)

    # Check the libvirtd log.
    result = utils_misc.wait_for(
        lambda: libvirt.check_logfile(expected_log, libvirtd_log_file), timeout=20)
    if not result:
        test.fail("Can't get expected log %s in %s" % (
            expected_log, libvirtd_log_file))

    # Check the memory allocation and memory device config.
    check_guest_xml(test, params, mem_index=1)

    # Check the memory info by virsh dominfo.
    check_guest_virsh_dominfo(vm, test, params, hot_plugged=True)

    # Check the guest memory.
    session = vm.wait_for_login()
    new_memtotal = utils_memory.memtotal(session)
    session.close()
    expected_memtotal = params.get('old_memtotal') + params.get("second_virtio_curr")
    if new_memtotal != expected_memtotal:
        test.fail("Memtotal is %s, should be %s " % (new_memtotal, expected_memtotal))
    test.log.debug("Check guest mem total successfully.")


def run(test, params, env):
    """
    Verify virtio-mem memory device hot-plug with different configs.
    """

    def setup_test():
        """
        Allocate memory on the host.
        """
        process.run(
            "echo %d > %s" % (allocate_size / default_hugepage_size,
                              kernel_hp_file % default_hugepage_size),
            shell=True)

    def run_test():
        """
        1. Define vm with virtio memory device.
        2. Hot plug another virtio memory.
        3. Check audit log, libvirtd log, memory allocation and memory device
         config.
        """
        test.log.info("TEST_STEP1: Define vm with virtio memory")
        memory_base.define_guest_with_memory_device(params, virtio_dict,
                                                    vm_attrs)

        test.log.info("TEST_STEP2: Start guest")
        vm.start()
        session = vm.wait_for_login()

        test.log.info("TEST_STEP3: Get the guest memory")
        params.update({'old_memtotal': utils_memory.memtotal(session)})
        session.close()

        test.log.info("TEST_STEP4: Check guest xml")
        check_guest_xml(test, params)

        test.log.info("TEST_STEP5: Check the memory info by virsh dominfo")
        check_guest_virsh_dominfo(vm, test, params)

        test.log.info("TEST_STEP6: Hot plug one virtio memory device")
        memory_base.plug_memory_and_check_result(
            test, params, mem_dict=plug_dict, operation='attach',
            expected_error=plug_error, expected_event=plug_event)

        if case in ["target_and_address", "source_mib_and_hugepages"]:
            test.log.info("TEST_STEP7: Check audit and libvirt log, "
                          "memory allocation and memory device config")
            check_after_attach(vm, test, params)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        process.run("echo 0 > %s" % (kernel_hp_file % default_hugepage_size),
                    shell=True)

    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_xml.copy()
    vm = env.get_vm(vm_name)

    virtio_dict, plug_dict = adjust_virtio_dict(params)
    default_hugepage_size = memory.get_huge_page_size()
    case = params.get("case")
    allocate_size = int(params.get("allocate_size"))
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    kernel_hp_file = params.get("kernel_hp_file")
    plug_error = params.get("plug_error")
    plug_event = params.get('plug_event')

    memory_base.check_supported_version(params, test, vm)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
