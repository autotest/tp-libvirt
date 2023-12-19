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

from avocado.utils import process

from virttest import virsh

from virttest.libvirt_xml.devices import memory
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirtd import Libvirtd

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


def get_virtio_mem(test, params):
    """
    Get 6 basic different virtio-mem memory devices.

    :param test: test object.
    :param params: dictionary with the test parameters.
    :return mem_list: virtio-mem attr dict list.
    """
    mem_list = []
    for item in [(None, None, 0), ('shared', 'yes', 0),
                 (None, None, 1), ('private', 'no', 1),
                 (None, None, 2), ('shared', 'yes', 2)]:

        single_mem = eval(params.get("mem_basic"))
        target = single_mem['target']
        target.update({'node': item[2]})

        if item[0] is not None:
            single_mem.update({'mem_access': item[0]})
        if item[1] is not None:
            single_mem.update({'mem_discard': item[1]})
        mem_list.append(single_mem)

    test.log.debug("Get all virtio-mem list:'%s'", mem_list)
    return mem_list


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
    virtio_mem_num = int(params.get("virtio_mem_num"))
    check_discard = params.get("check_discard")
    discard_error_msg = params.get("discard_error_msg")
    mem_name_list = []

    # Check access share setting
    ret = virsh.qemu_monitor_command(vm.name, "info memdev", "--hmp",
                                     debug=True).stdout_text.replace("\r\n", "")
    for index in range(virtio_mem_num):
        mem_name = "memvirtiomem%d" % index
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
    Verify virtio-mem memory device works with access and discard settings.
    """
    def run_test():
        """
        1.Define vm with virtio-mem device
        2.Check the virtio-mem memory device access and discard setting
        3.Define vm without virtio-mem device and restart service, and hotplug
        virtio-mem device.
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

        test.log.info("TEST_STEP3,4: Check virtio-mem access and discard")
        check_access_and_discard(test, params, vm, expected_share,
                                 expected_discard)

        test.log.info("TEST_STEP5: Destroy vm")
        vm.destroy()

        test.log.info("TEST_STEP6: Check the host path for memory file backing")
        res = process.run(check_path % vm_name, shell=True, ignore_status=True).stderr_text
        if not re.search(path_error, res):
            test.fail("Expected '%s', but got '%s'" % (path_error, res))

        test.log.info("TEST_STEP7: Define guest without virtio-mem memory")
        bkxml.setup_attrs(**vm_attrs)
        bkxml.sync()
        vm.start()

        test.log.info("TEST_STEP8: Restart service")
        Libvirtd().restart()

        test.log.info("TEST_STEP9: Hot plug all memory device")
        for mem in virtio_mems:
            virtio_mem = memory.Memory()
            virtio_mem.setup_attrs(**mem)
            virsh.attach_device(vm_name, virtio_mem.xml,
                                debug=True, ignore_status=False)

        test.log.info("TEST_STEP10: Check discard and access again.")
        check_access_and_discard(test, params, vm,
                                 expected_share, expected_discard)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    expected_share = eval(params.get("expected_share"))
    expected_discard = eval(params.get("expected_discard"))
    check_path = params.get("check_path")
    path_error = params.get("path_error", "No such file or directory")

    try:
        memory_base.check_supported_version(params, test, vm)
        run_test()

    finally:
        teardown_test()
