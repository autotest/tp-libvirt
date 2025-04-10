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


def adjust_dimm_dict(params):
    """
    Adjust dimm memory dict and plugging dimm dict.

    :param params: dictionary with the test parameters
    """
    source_dict = params.get("source_dict", "")
    addr_dict = params.get("addr_dict", "")
    plug_addr = params.get("plug_addr", "")
    dimm_dict = eval(params.get("dimm_dict", "{}"))
    plug_dimm_dict = eval(params.get("plug_dimm_dict", "{}"))
    default_hugepage_size = memory.get_huge_page_size()

    if source_dict:
        dimm_dict['source'] = eval(source_dict % default_hugepage_size)
        plug_dimm_dict['source'] = eval(source_dict % default_hugepage_size)
    if addr_dict:
        dimm_dict['address'] = eval(addr_dict)
        plug_dimm_dict['address'] = eval(plug_addr)
    params.update({'dimm_dict': dimm_dict})
    params.update({'plug_dimm_dict': plug_dimm_dict})


def adjust_size_unit(params, plugged=False):
    """
    Adjust the unit of dimm target size and plugged dimm target size.

    :param params: cartesian config parameters.
    :param plugged: the flag of after plugging.
    :return target_size, plugged_size, init define dimm target size and
    plugged dimm target size
    """
    plug_dimm_type = params.get("plug_dimm_type")
    if plug_dimm_type in ["source_and_mib", "zero_memory_unit_gb"]:
        target_size = memory_base.convert_data_size(
            params.get("target_size") + params.get('size_unit'), 'KiB')
    else:
        target_size = params.get("target_size")

    if plugged:
        if plug_dimm_type in ["source_and_mib", "zero_memory_unit_gb"]:
            plugged_size = memory_base.convert_data_size(
                params.get("plug_target_size") + params.get('plug_size_unit'), 'KiB')
        else:
            plugged_size = params.get('plug_target_size')
    else:
        plugged_size = 0
    return int(target_size), int(plugged_size)


def attach_dimm_and_check_result(test, params):
    """
    Attach dimm memory and check result.

    :param test: test object.
    :param params: dictionary with the test parameters.
    """
    plug_error = params.get('plug_error')
    plug_event = params.get('plug_event')
    plug_dict = params.get("plug_dimm_dict")
    memory_base.plug_memory_and_check_result(
        test, params, mem_dict=plug_dict, operation='attach',
        expected_error=plug_error, expected_event=plug_event)


def check_guest_virsh_dominfo(vm, test, params, plugged=False):
    """
    Check current memory value and memory value in virsh dominfo result.

    :param vm: vm object.
    :param test: test object.
    :param params: dictionary with the test parameters.
    :param plugged: the flag of checking after plugging dimm.
    """
    mem_value = int(params.get("mem_value"))
    current_mem = int(params.get("current_mem"))
    target_size, plugged_size = adjust_size_unit(params, plugged=plugged)
    expected_mem = str(mem_value + target_size + plugged_size)
    expected_curr = str(current_mem + plugged_size)

    memory_base.check_dominfo(vm, test, expected_mem, expected_curr)


def check_after_attach(vm, test, params):
    """
    Check the below points after plugging or unplugging
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
    plug_slot = params.get("plug_slot")
    libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
    target_size, plugged_size = adjust_size_unit(params, plugged=True)
    base_xpath, dimm_xpath = params.get("base_xpath"), params.get("dimm_xpath")
    ausearch_check = params.get("ausearch_check") % (
        mem_value+target_size,  mem_value+target_size+plugged_size)

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
        vmxml, eval(base_xpath % (mem_value + target_size + plugged_size,
                                  current_mem + plugged_size)))
    libvirt_vmxml.check_guest_xml_by_xpaths(
        vmxml.devices.by_device_tag("memory")[1], eval(dimm_xpath % (plugged_size, plug_slot)))

    # Check the memory info by virsh dominfo.
    check_guest_virsh_dominfo(vm, test, params, plugged=True)

    # Check the guest memory.
    session = vm.wait_for_login()
    new_memtotal = utils_memory.memtotal(session)
    session.close()
    expected_memtotal = params.get('old_memtotal') + plugged_size
    if new_memtotal != expected_memtotal:
        test.fail("Memtotal is %s, should be %s " % (new_memtotal, expected_memtotal))
    test.log.debug("Check Memtotal successfully.")


def run(test, params, env):
    """
    Verify dimm memory device hot-plug with different configs.
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
        2. Hotplug dimm memory.
        3. Check audit log, libvirtd log, memory allocation and memory device
         config.
        """
        test.log.info("TEST_STEP1: Define vm with dimm memory")
        memory_base.define_guest_with_memory_device(params, params.get("dimm_dict"), vm_attrs)

        test.log.info("TEST_STEP2: Start guest")
        vm.start()
        session = vm.wait_for_login()

        test.log.info("TEST_STEP3: Get the guest memory")
        params.update({'old_memtotal': utils_memory.memtotal(session)})
        session.close()

        test.log.info("TEST_STEP4: Check the memory allocation and dimm config")
        target_size, _ = adjust_size_unit(params)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(
            vmxml, eval(base_xpath % (mem_value + target_size, current_mem)))
        libvirt_vmxml.check_guest_xml_by_xpaths(
            vmxml.devices.by_device_tag("memory")[0], eval(dimm_xpath % (target_size, slot)))

        test.log.info("TEST_STEP5: Check the memory info by virsh dominfo")
        check_guest_virsh_dominfo(vm, test, params)

        test.log.info("TEST_STEP6: Hot plug one dimm memory device")
        attach_dimm_and_check_result(test, params)

        if plug_dimm_type in ["target_and_address", "source_and_mib"]:
            test.log.info("TEST_STEP7:Check audit and libvirt log, memory "
                          "allocation and memory device config ")
            check_after_attach(vm, test, params)

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
    adjust_dimm_dict(params)

    slot = params.get("slot")
    kernel_hp_file = params.get("kernel_hp_file")
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    allocate_size = int(params.get("allocate_size"))
    mem_value = int(params.get("mem_value"))
    current_mem = int(params.get("current_mem"))
    base_xpath, dimm_xpath = params.get("base_xpath"), params.get("dimm_xpath")
    plug_dimm_type = params.get("plug_dimm_type")
    default_hugepage_size = memory.get_huge_page_size()

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
