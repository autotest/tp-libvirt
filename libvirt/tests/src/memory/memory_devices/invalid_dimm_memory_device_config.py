# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest.utils_test import libvirt

from provider.numa import numa_base


def get_pagesize_value(params):
    """
    Get pagesize value

    :param params: Dictionary with the test parameters
    :return: page_size, The page size to set in original vmxml
    """
    invalid_pagesize = params.get("invalid_pagesize")
    pagesize_cmd = params.get("pagesize_cmd")

    if invalid_pagesize:
        page_size = invalid_pagesize
    else:
        page_size = process.run(pagesize_cmd, ignore_status=True,
                                shell=True).stdout_text.strip()
    return int(page_size)


def get_mem_obj(mem_dict):
    """
    Get mem object.

    :param mem_dict: memory dict value.
    :return mem_obj: object of memory devices.
    """
    mem_obj = memory.Memory()
    mem_obj.setup_attrs(**eval(mem_dict))
    return mem_obj


def define_guest(test, params, page_size):
    """
    Define guest with specific

    :param test: test object.
    :param params: dict, test parameters.
    :param page_size: page size value in vm xml.
    """
    vm_name = params.get("main_vm")
    vm_attrs = eval(params.get("vm_attrs"))
    dimm_dict = params.get("dimm_dict")
    invalid_setting = params.get("invalid_setting")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml.setup_attrs(**vm_attrs)

    devices = vmxml.get_devices()
    mem_obj = get_mem_obj(dimm_dict % page_size)
    devices.append(mem_obj)
    vmxml.set_devices(devices)
    test.log.debug("Define vm with %s." % vmxml)

    # Check libvirt version
    if libvirt_version.version_compare(9, 0, 0) and \
            invalid_setting == "unexisted_node":
        define_error = params.get("start_vm_error")
    elif not libvirt_version.version_compare(9, 0, 0) and \
            invalid_setting == "invalid_addr_type":
        define_error = params.get("define_error_8")
    else:
        define_error = params.get("define_error")
    # Redefine define_error for checking start_vm_error
    params.update({"define_error": define_error})

    # Define guest
    try:
        vmxml.sync()
    except Exception as e:
        if define_error:
            if define_error not in str(e):
                test.fail("Expect to get '%s' error, but got '%s'" % (define_error, e))
        else:
            test.fail("Expect define successfully, but failed with '%s'" % e)


def run(test, params, env):
    """
    Verify error messages prompt with invalid memory device configs

    1.invalid value:
     exceed slot number, max address base, nonexistent guest node
     nonexistent node mask, invalid pagesize, invalid address type
    2.memory setting: with numa
    """

    def setup_test():
        """
        Check host has at least 2 numa nodes.
        """
        test.log.info("TEST_SETUP: Check the numa nodes")
        numa_obj = numa_base.NumaTest(vm, params, test)
        numa_obj.check_numa_nodes_availability()

    def run_test():
        """
        Define vm with dimm.
        Start vm.
        Hotplug dimm.
        """
        test.log.info("TEST_STEP1: Define vm and check result")
        source_pagesize = get_pagesize_value(params)
        define_guest(test, params, source_pagesize)

        test.log.info("TEST_STEP2: Start guest ")
        start_result = virsh.start(vm_name, ignore_status=True)
        if start_vm_error:
            # Start success for unexisted_node scenario and version>9.0
            if libvirt_version.version_compare(9, 0, 0) and \
                    invalid_setting == "unexisted_node":
                libvirt.check_exit_status(start_result)
            else:
                libvirt.check_result(start_result, start_vm_error)

        test.log.info("TEST_STEP3: Start guest without dimm devices")
        original_xml.setup_attrs(**vm_attrs)
        test.log.debug("Define vm by '%s' \n", original_xml)
        original_xml.sync()
        virsh.start(vm_name, debug=True, ignore_status=False)

        test.log.info("TEST_STEP4: Hotplug dimm memory device")
        attach_error = params.get("start_vm_error", params.get("define_error"))

        mem_obj = get_mem_obj(dimm_dict % source_pagesize)
        result = virsh.attach_device(vm_name, mem_obj.xml, debug=True).stderr_text
        if attach_error not in result:
            test.fail("Expected get error '%s', but got '%s'" % (attach_error, result))

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_xml.copy()
    dimm_dict = params.get("dimm_dict")
    invalid_setting = params.get("invalid_setting")
    vm_attrs = eval(params.get("vm_attrs"))
    start_vm_error = params.get("start_vm_error")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
