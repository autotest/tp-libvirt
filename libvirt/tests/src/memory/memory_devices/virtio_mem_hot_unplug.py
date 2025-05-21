# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os
import uuid

from avocado.utils import memory

from virttest import virsh
from virttest import utils_misc
from virttest import utils_sys
from virttest import utils_test
from virttest import test_setup
from virttest.libvirt_xml import vm_xml
from virttest.staging import utils_memory
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_vmxml

from provider.memory import memory_base

virsh_dargs = {"ignore_status": False, "debug": True}
default_hugepage_size = memory.get_huge_page_size()


def adjust_virtio_dict(params):
    """
    Adjust virtio memory dict and unplugging dict.

    :param params: dictionary with the test parameters.
    :return virtio_dict, unplug_dict , the virtio memory dict when define guest
     and unplug.
    """
    alias_name = "ua-" + str(uuid.uuid1())
    params.update({"alias": alias_name})

    source_dict = params.get("source_dict", "")
    virtio_dict, unplug_dict = params.get("virtio_dict"), params.get("unplug_dict")
    virtio_dict = eval(virtio_dict % (alias_name, default_hugepage_size))
    unplug_dict = eval(unplug_dict % (alias_name, default_hugepage_size))

    if source_dict:
        virtio_dict['source'] = eval(source_dict % default_hugepage_size)
        unplug_dict['source'] = eval(source_dict % default_hugepage_size)

    return virtio_dict, unplug_dict


def adjust_virtio_size(params, test):
    """
    Adjust all virtio related size to KiB.

    :param params: dict wrapped with params.
    :param test: test object.
    :return target_size, request_size, unplug_target_size, unplug_request_size.
    virtio target size, virtio requested size, unplugged virtio target size,
    unplugged virtio requested size.
    """
    unplug_target_size = int(params.get('unplug_target_size', 0))
    unplug_request_size = int(params.get('unplug_request_size', 0))
    target_size, request_size = int(params.get('target_size')), int(
        params.get('request_size'))
    unplug_size_unit = params.get('unplug_size_unit')
    unplug_request_unit = params.get('unplug_request_unit')
    size_unit, request_unit = params.get('size_unit'), params.get(
        'request_unit')

    def _convert_size(curr_size, curr_unit, item):
        if curr_unit != "KiB":
            new_size = memory_base.convert_data_size(str(curr_size) + curr_unit)
            test.log.debug("Convert %s %s to be %s", item, curr_size, new_size)
            return int(new_size)
        else:
            return int(curr_size)
    target_size = _convert_size(target_size, size_unit, "target_size")
    request_size = _convert_size(request_size, request_unit, "request_size")
    unplug_target_size = _convert_size(unplug_target_size, unplug_size_unit, "unplug_target_size")
    unplug_request_size = _convert_size(unplug_request_size, unplug_request_unit, "unplug_request_size")
    return target_size, request_size, unplug_target_size, unplug_request_size


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
    test.log.debug("Checked the %s successfully", item_name)


def check_source_and_addr_xml(test, params, virtio_mem_xml):
    """
    Check virtio memory source and address xml if existed.

    :param test: test object
    :param params: dictionary with the test parameters
    :param virtio_mem_xml, virtio memory xml
    """
    expected_base = params.get("base")
    addr_dict = params.get("addr_dict", "")
    source_dict = params.get("source_dict", "")
    expected_source_pgsize = default_hugepage_size

    if source_dict:
        if virtio_mem_xml.source.pagesize != expected_source_pgsize:
            test.fail("Got virtio memory source pagesize %s, should be %s" % (
                virtio_mem_xml.source.pagesize, expected_source_pgsize))
        test.log.debug("Check virtio memory source xml successfully")
    if addr_dict:
        actual_base = virtio_mem_xml.target.address.attrs.get("base")
        if actual_base != expected_base:
            test.fail("Got virtio memory address base %s, should be %s" % (
                actual_base, expected_base))
        test.log.debug("Check virtio memory address xml successfully")


def check_guest_xml(test, params, hot_unplugged=False):
    """
    Check guest xml.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param hot_unplugged: boolean, the flag of hot unplugging or not.

    """
    case = params.get("case")
    vm_name = params.get("main_vm")
    mem_value = int(params.get("mem_value"))
    current_mem = int(params.get("current_mem"))
    with_numa = params.get('with_numa', 'yes') == 'yes'

    target_size, request_size, unplug_target_size, unplug_request_size = \
        adjust_virtio_size(params, test)
    expected_virito_curr = 0 if case == "none_zero_request" else request_size

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    acutal_mem = vmxml.memory
    acutal_curr = vmxml.current_mem
    virtio_mem_xml = vmxml.devices.by_device_tag("memory")

    if not hot_unplugged:
        acutal_virtio_target = virtio_mem_xml[0].target.size
        acutal_virtio_block = virtio_mem_xml[0].target.block_size
        acutal_virtio_requested = virtio_mem_xml[0].target.requested_size
        acutal_virtio_current = virtio_mem_xml[0].target.current_size
        if with_numa:
            params.update({"expected_mem0": mem_value + target_size})
            params.update({"expected_curr0": current_mem + acutal_virtio_current})
        else:
            params.update({"expected_mem0": mem_value})
            params.update({"expected_curr0": current_mem - target_size + acutal_virtio_current})
        params.update({"curr1": acutal_virtio_current})

        check_source_and_addr_xml(test, params, virtio_mem_xml[0])
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
            test, expected_virito_curr, acutal_virtio_current, 'virtio memory current size')

    else:
        if virtio_mem_xml:
            test.fail("Guest virtio memory xml is not empty, but got %s", virtio_mem_xml)
        test.log.debug("Check unplugged virtio memory unexisted successfully")

        params.update(
            {"expected_mem1": params.get("expected_mem0") - unplug_target_size})
        params.update(
            {"expected_curr1": params.get("expected_curr0") - params.get("curr1")})

        compare_two_values(test, params.get("expected_mem1"), acutal_mem, 'memory')
        compare_two_values(test, params.get("expected_curr1"), acutal_curr, 'current memory')


def check_guest_virsh_dominfo(vm, test, params, hot_unplugged=False):
    """
    Check memory value and current memory value in virsh dominfo result.

    :param vm: vm object.
    :param test: test object.
    :param params: dictionary with the test parameters.
    :param hot_unplugged: boolean, the flag of hot unplugging or not.
    """
    if hot_unplugged:
        expected_mem = params.get("expected_mem1")
        expected_curr = params.get("expected_curr1")
    else:
        expected_mem = params.get("expected_mem0")
        expected_curr = params.get("expected_curr0")
    memory_base.check_dominfo(vm, test, str(expected_mem), str(expected_curr))


def check_after_detach(vm, test, params):
    """
    Check the below points after unplugging.

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
    expected_log = params.get("expected_log")
    audit_cmd = params.get("audit_cmd")
    target_size, request_size, unplug_target_size, unplug_request_size = \
        adjust_virtio_size(params, test)
    libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
    ausearch_check = params.get("ausearch_check") % (
        params.get("expected_mem0"), params.get("expected_mem0") - unplug_target_size)
    # Check the audit log by ausearch.
    utils_sys.check_audit_log(audit_cmd, ausearch_check)

    # Check the libvirtd log.
    result = utils_misc.wait_for(
        lambda: libvirt.check_logfile(expected_log, libvirtd_log_file), timeout=20)
    if not result:
        test.fail("Can't get expected log %s in %s" % (
            expected_log, libvirtd_log_file))

    # Check the memory allocation and memory device config.
    check_guest_xml(test, params, hot_unplugged=True)

    # Check the memory info by virsh dominfo.
    check_guest_virsh_dominfo(vm, test, params, hot_unplugged=True)

    # Check the guest memory.
    session = vm.wait_for_login()
    new_memtotal = utils_memory.memtotal(session)
    session.close()
    expected_memtotal = params.get('old_memtotal') - params.get("curr1")
    if new_memtotal != expected_memtotal:
        test.fail("Memtotal is %s, should be %s " % (new_memtotal, expected_memtotal))
    test.log.debug("Check guest mem total successfully.")


def run(test, params, env):
    """
    Verify virtio-mem memory device hot-unplug with different configs.
    """
    def setup_test():
        """
        Allocate memory on the host, add kernel parameter to guest.
        """
        if case == "source_mib_and_hugepages":
            if not libvirt_vmxml.check_guest_machine_type(vmxml, machine_version):
                test.fail("Guest config machine should be >= rhel{}".format(
                    machine_version))
            hpc.setup()
        if kernel_params_add:
            utils_test.update_boot_option(
                vm, args_added=kernel_params_add, guest_arch_name=vm_arch_name)

    def run_test():
        """
        1. Define vm with virtio memory device.
        2. Hot unplug another virtio memory.
        3. Check audit log, libvirtd log, memory allocation and memory device
         config.
        """
        test.log.info("TEST_STEP1: Define vm with virtio memory")
        memory_base.define_guest_with_memory_device(params, virtio_dict, vm_attrs)

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

        if case in ["target_and_address", "source_mib_and_hugepages"]:
            test.log.info("TEST_STEP6: Update the requested memory size to 0")
            virsh.update_memory_device(
                vm_name, options=updated_request_option, wait_for_event=True,
                **virsh_dargs)

        test.log.info("TEST_STEP7: Hot unplug one virtio memory device")
        memory_base.plug_memory_and_check_result(
            test, params, mem_dict=unplug_dict, alias=params.get("alias"),
            operation=detach_method, expected_error=unplug_error,
            expected_event=unplug_event, event_timeout=15)

        if case in ["target_and_address", "source_mib_and_hugepages"]:
            test.log.info("TEST_STEP8: Check audit and libvirt log, "
                          "memory allocation and memory device config")
            check_after_detach(vm, test, params)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        hpc.cleanup()
        if kernel_params_remove:
            if not vm.is_alive():
                vm.start()
            utils_test.update_boot_option(
                vm, args_removed=kernel_params_remove, guest_arch_name=vm_arch_name)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    memory_base.check_supported_version(params, test, vm)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    virtio_dict, unplug_dict = adjust_virtio_dict(params)
    case = params.get("case")
    machine_version = params.get("machine_version")
    allocate_size = int(params.get("allocate_size"))
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    kernel_hp_file = params.get("kernel_hp_file")
    updated_request_option = params.get("updated_request_option")
    detach_method = params.get("detach_method")
    unplug_error = params.get("unplug_error")
    unplug_event = params.get('unplug_event')
    kernel_params_add = params.get('kernel_params_add')
    kernel_params_remove = params.get('kernel_params_remove')
    vm_arch_name = params.get('vm_arch_name')

    params.update({"kernel_hp_file": kernel_hp_file % default_hugepage_size})
    params.update({"target_hugepages": allocate_size / default_hugepage_size})
    hpc = test_setup.HugePageConfig(params)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
