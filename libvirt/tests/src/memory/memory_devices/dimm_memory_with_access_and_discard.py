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

from avocado.utils import memory
from avocado.utils import process

from virttest import utils_test
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_libvirt import libvirt_vmxml

virsh_dargs = {"ignore_status": False, "debug": True}


def get_vm_attrs(test, params):
    """
    Get vm attrs.

    :param test: test object
    :param params: dictionary with the test parameters
    :return vm_attrs: get updated vm attrs dict.
    """
    vm_attrs = eval(params.get("vm_attrs", "{}"))
    source_attr = params.get("source_attr", "")
    discard_attr = params.get("discard_attr", "")
    access_attr = params.get("access_attr", "")

    mb_value = ""
    for item in [source_attr, discard_attr, access_attr]:
        if item != "":
            mb_value += item + ","
    mb_attrs = eval("{'mb':{%s}}" % mb_value[:-1])

    vm_attrs.update(mb_attrs)
    test.log.debug("Get current vm attrs is :%s", vm_attrs)

    return vm_attrs


def create_dimm_objects(test, params):
    """
    Get 6 basic different dimm memory devices object.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :return mem_list: dimm memory device object list.
    """
    default_hugepage_size = memory.get_huge_page_size()
    mem_list = []
    for item in [(None, None, 0), ('shared', 'yes', 0),
                 (None, None, 1), ('private', 'no', 1),
                 (None, None, 2), ('shared', 'yes', 2)]:

        single_mem = eval(params.get("dimm_basic") % default_hugepage_size)
        target = single_mem['target']
        target.update({'node': item[2]})

        if item[0] is not None:
            single_mem.update({'mem_access': item[0]})
        if item[1] is not None:
            single_mem.update({'mem_discard': item[1]})
        test.log.debug("Get one dimm dict is:'%s'\n", single_mem)

        dimm_obj = libvirt_vmxml.create_vm_device_by_type(
            'memory', single_mem)
        mem_list.append(dimm_obj)

    return mem_list


def plug_dimm(test, params, vm, operation):
    """
    Hot plug or Cold plug dimm

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param vm: vm object.
    :param operation: flag for hot plugging or cold plugging.
    """
    vm_name = params.get("main_vm")
    attach_option = params.get("attach_option")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml.remove_all_device_by_type("memory")

    all_dimms = create_dimm_objects(test, params)
    if operation == "cold_plug":
        pass
    elif operation == "hot_plug":
        vm.start()
        vm.wait_for_login().close()
        Libvirtd().restart()

    for mem in all_dimms:
        virsh.attach_device(vm_name, mem.xml,
                            flagstr=attach_option,
                            **virsh_dargs)


def check_access_and_discard(test, params, vm,
                             expected_share, expected_discard):
    """
    Check access and discard setting.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :param vm: vm object.
    :param expected_share: expected access shared value list.
    :param expected_discard: expected discard value list.
    """
    dimm_mem_num = int(params.get("dimm_mem_num"))
    check_discard = params.get("check_discard")
    discard_error_msg = params.get("discard_error_msg")
    mem_name_list = []

    if not vm.is_alive():
        vm.start()
    # Check access share setting
    ret = virsh.qemu_monitor_command(vm.name, "info memdev", "--hmp",
                                     debug=True).stdout_text.replace("\r\n", "")
    for index in range(dimm_mem_num):
        mem_name = "memdimm%d" % index
        pattern = "memory backend: %s.*share: %s " % (mem_name, expected_share[index])
        if not re.search(pattern, ret):
            test.fail("Expect '%s' exist, but not found" % pattern)
        else:
            test.log.debug("Check access shared value is '%s': PASS", pattern)
        mem_name_list.append(mem_name)

    # Check discard setting
    for index, mem_name in enumerate(mem_name_list):
        res = virsh.qemu_monitor_command(
            vm.name, check_discard % mem_name, debug=True).stdout_text

        if 'return' in json.loads(res):
            actual_discard = str(json.loads(res)['return'])
            if actual_discard != expected_discard[index]:
                test.fail("Expect discard is '%s', but got '%s'" % (
                    expected_discard[index], actual_discard))
            else:
                test.log.debug("Check discard value is '%s' PASS", actual_discard)
        else:
            actual_error = json.loads(res)['error']['desc']
            if not re.search(discard_error_msg, actual_error):
                test.fail("Expected to get '%s' in '%s'" % (discard_error_msg,
                                                            actual_error))
            else:
                test.log.debug("Check '%s' PASS ", actual_error)


def run(test, params, env):
    """
    Verify dimm memory device works with access and discard settings.
    """
    def setup_test():
        """
        Set kernel parameter memhp_default_state to online_movable(
        this would make hotunplug more reliable)
        """
        utils_test.update_boot_option(vm, args_added=kernel_params_add,
                                      need_reboot=True)
        vm.destroy()

    def run_test():
        """
        1.Define vm with dimm memory device.
        2.Check the dimm memory device access and discard setting.
        3.Define vm without dimm memory device and restart service, and hotplug
        dimm device.
        """
        test.log.info("TEST_STEP1: Define vm with numa")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm_attrs = get_vm_attrs(test, params)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()

        test.log.info("TEST_STEP2-5: Plug dimm memory devices")
        plug_dimm(test, params, vm, operation)

        test.log.info("TEST_STEP6,7: Check dimm memory access and discard")
        check_access_and_discard(test, params, vm, expected_share,
                                 expected_discard)

        test.log.info("TEST_STEP8:Hot unplug all dimm memory device")
        for dimm in create_dimm_objects(test, params):
            virsh.detach_device(vm_name, dimm.xml, **virsh_dargs)

        test.log.info("TEST_STEP9: Destroy vm")
        vm.destroy()

        test.log.info("TEST_STEP10: Check the host path for memory file backing")
        res = process.run(check_path % vm_name, shell=True, ignore_status=True).stderr_text
        if not re.search(path_error, res):
            test.fail("Expected '%s', but got '%s'" % (path_error, res))

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        if not vm.is_alive():
            vm.start()
        utils_test.update_boot_option(vm, args_removed=kernel_params_add)
        bkxml.sync()

    vm_name = params.get("main_vm")
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_xml.copy()
    vm = env.get_vm(vm_name)

    operation = params.get("operation")
    kernel_params_add = params.get("kernel_params_add")
    expected_share = eval(params.get("expected_share"))
    expected_discard = eval(params.get("expected_discard"))
    check_path = params.get("check_path")
    path_error = params.get("path_error", "No such file or directory")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
