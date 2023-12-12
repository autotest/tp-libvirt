# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from avocado.utils import process

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest.utils_test import libvirt

from provider.numa import numa_base
from provider.memory import memory_base


def get_page_size(params):
    """
    Get pagesize value

    :param params: Dictionary with the test parameters
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

    :param mem_dict: memory dict value
    :return: mem_obj, memory object.
    """
    mem_obj = memory.Memory()
    mem_obj.setup_attrs(**eval(mem_dict))
    return mem_obj


def define_guest(test, params, page_size):
    """
    Define guest with specific

    :param test: test object
    :param params: Dictionary with the test parameters.
    :params: page_size, the pagesize that used when define guest.
    """
    vm_name = params.get("main_vm")
    vm_attrs = eval(params.get("vm_attrs"))
    mem_dict = params.get("mem_dict")
    define_error = params.get("define_error")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml.setup_attrs(**vm_attrs)

    virtio_mem = get_mem_obj(mem_dict % page_size)
    vmxml.devices = vmxml.devices.append(virtio_mem)
    test.log.debug("Define vm with %s." % vmxml)

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
    Verify error messages prompt with invalid virtio-mem device configs

    1.invalid value:
     over committed requested memory, max address base, nonexistent guest node
     nonexistent node mask, invalid pagesize, small block size, invalid block size
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
        Define vm with virtio-mem and start vm.
        Define vm without virtio-mem and hotplug virtio-mem.
        """
        test.log.info("TEST_STEP1: Define vm with virio-mem")
        source_pagesize = get_page_size(params)
        define_guest(test, params, source_pagesize)

        test.log.info("TEST_STEP2: Start guest")
        start_result = virsh.start(vm_name, ignore_status=True, debug=True)
        libvirt.check_result(start_result, start_vm_error)

        test.log.info("TEST_STEP3: Start guest without virtio-mem")
        original_vmxml.setup_attrs(**vm_attrs)
        test.log.debug("Define vm without virtio-mem by '%s' \n", original_vmxml)
        original_vmxml.sync()
        virsh.start(vm_name, debug=True)

        test.log.info("TEST_STEP4: Hotplug virtio-mem memory device")
        mem_obj = get_mem_obj(mem_dict % source_pagesize)
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
    original_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_vmxml.copy()
    mem_dict = params.get("mem_dict")
    vm_attrs = eval(params.get("vm_attrs"))
    start_vm_error = params.get("start_vm_error")
    attach_error = params.get("start_vm_error", params.get("define_error"))

    try:
        memory_base.check_supported_version(params, test, vm)
        setup_test()
        run_test()

    finally:
        teardown_test()
