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
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.memory import memory_base
from virttest.staging import utils_memory

virsh_dargs = {"ignore_status": False, "debug": True}


def adjust_size_unit(params):
    """
    Adjust the unit of dimm target size.

    :param params: cartesian config parameters.
    :return target_size, init define dimm target size.
    """
    plug_dimm_type = params.get("plug_dimm_type")
    if plug_dimm_type in ["source_and_mib"]:
        target_size = memory_base.convert_data_size(
            params.get("target_size") + params.get('size_unit'), 'KiB')
    else:
        target_size = params.get("target_size")

    return int(target_size)


def check_guest_virsh_dominfo(vm, test, params, unplugged=False):
    """
    Check current memory value and memory value in virsh dominfo result.

    :param vm: vm object.
    :param test: test object.
    :param params: dictionary with the test parameters.
    :param unplugged: boolean, the flag of unplugging.
    """
    mem_value = int(params.get("mem_value"))
    current_mem = int(params.get("current_mem"))

    target_size = adjust_size_unit(params)
    if unplugged:
        expected_mem = str(mem_value)
        expected_curr = str(current_mem - target_size)
    else:
        expected_mem = str(mem_value + target_size)
        expected_curr = str(current_mem)

    memory_base.check_dominfo(vm, test, expected_mem, expected_curr)


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
    mem_value = int(params.get("mem_value"))
    current_mem = int(params.get("current_mem"))
    expected_log = params.get("expected_log")
    audit_cmd = params.get("audit_cmd")
    base_xpath, dimm_xpath = params.get("base_xpath"), params.get("dimm_xpath")

    target_size = adjust_size_unit(params)
    libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
    ausearch_check = params.get("ausearch_check") % (mem_value+target_size, mem_value)

    # Check the audit log by ausearch.
    ausearch_result = process.run(audit_cmd, shell=True)
    libvirt.check_result(ausearch_result, expected_match=ausearch_check)
    test.log.debug("Check audit log %s successfully." % ausearch_check)

    # Check the libvirtd log.
    result = utils_misc.wait_for(
        lambda: libvirt.check_logfile(expected_log, libvirtd_log_file), timeout=20)
    if not result:
        test.fail("Can't get expected log %s in %s" % (expected_log, libvirtd_log_file))

    # Check the memory allocation and memory device config.
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    libvirt_vmxml.check_guest_xml_by_xpaths(
        vmxml, eval(base_xpath % (mem_value, current_mem-target_size)))
    if vmxml.devices.by_device_tag("memory"):
        test.fail("Dimm memory still exist after unplugging")

    # Check the memory info by virsh dominfo.
    check_guest_virsh_dominfo(vm, test, params, unplugged=True)

    # Check the guest memory.
    session = vm.wait_for_login()
    new_memtotal = utils_memory.memtotal(session)
    session.close()
    expected_memtotal = params.get('old_memtotal') - target_size
    if new_memtotal != expected_memtotal:
        test.fail("Memtotal is %s, should be %s " % (new_memtotal, expected_memtotal))
    test.log.debug("Check Memtotal successfully.")


def run(test, params, env):
    """
    Verify dimm memory device hot unplug with different configs.
    """
    def setup_test():
        """
        Allocate memory on the host.
        """
        process.run("echo %d > %s" % (
            allocate_size / default_hugepage_size,
            kernel_hp_file % default_hugepage_size), shell=True)

    def run_test():
        """
        1. Define vm with dimm memory device.
        2. Hot unplug dimm memory.
        3. Check audit log, libvirtd log, memory allocation and memory device
         config.
        """
        test.log.info("TEST_STEP1: Define vm with dimm memory")
        memory_base.define_guest_with_memory_device(params, dimm_dict, vm_attrs)

        test.log.info("TEST_STEP2: Start guest")
        vm.start()
        session = vm.wait_for_login()

        test.log.info("TEST_STEP3: Get the guest memory")
        params.update({'old_memtotal': utils_memory.memtotal(session)})
        session.close()

        test.log.info("TEST_STEP4: Check the memory allocation and dimm config")
        target_size = adjust_size_unit(params)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(
            vmxml, eval(base_xpath % (mem_value + target_size, current_mem)))
        libvirt_vmxml.check_guest_xml_by_xpaths(
            vmxml.devices.by_device_tag("memory")[0], eval(dimm_xpath % (target_size, slot)))

        test.log.info("TEST_STEP5: Check the memory info by virsh dominfo")
        check_guest_virsh_dominfo(vm, test, params)

        test.log.info("TEST_STEP6: Hot unplug one dimm memory device")
        memory_base.plug_memory_and_check_result(
            test, params, mem_dict=unplug_dict, operation='detach',
            expected_error=unplug_error, expected_event=unplug_event,
            event_timeout=20)

        if mem_state in ["online_movable_mem"]:
            test.log.info("TEST_STEP7: Check audit and libvirt log, "
                          "memory allocation and memory device config")
            check_after_detach(vm, test, params)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        process.run("echo 0 > %s" % (kernel_hp_file % default_hugepage_size), shell=True)

    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_xml.copy()
    vm = env.get_vm(vm_name)
    default_hugepage_size = memory.get_huge_page_size()

    slot = params.get("slot")
    plug_dimm_type = params.get("plug_dimm_type")
    if plug_dimm_type == "source_and_mib":
        dimm_dict = eval(params.get("dimm_dict") % default_hugepage_size)
        unplug_dict = dimm_dict
    else:
        dimm_dict = eval(params.get("dimm_dict"))
        unplug_dict = eval(params.get("unplug_dimm_dict", "{}"))

    unplug_error = params.get('unplug_error')
    unplug_event = params.get('unplug_event')
    kernel_hp_file = params.get("kernel_hp_file")
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    allocate_size = int(params.get("allocate_size"))
    mem_value = int(params.get("mem_value"))
    current_mem = int(params.get("current_mem"))
    base_xpath, dimm_xpath = params.get("base_xpath"), params.get("dimm_xpath")
    mem_state = params.get("mem_state")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
