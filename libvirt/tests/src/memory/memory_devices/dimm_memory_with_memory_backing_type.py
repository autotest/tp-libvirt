# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import json
import re

from avocado.utils import memory as avocado_mem

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.staging import utils_memory
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Verify dimm memory device works with various memory backing type
    """
    def setup_test():
        """
        Test setup:
        1. Set up hugepage
        2. Build dimm memory device list
        3. Build memory backing config
        """
        nonlocal dimm_list, memory_backing_dict
        test.log.info("TEST_SETUP: Set up hugepage")
        default_pagesize = avocado_mem.get_huge_page_size()
        hp_num = int(hugepage_memory) // default_pagesize
        utils_memory.set_num_huge_pages(hp_num)

        test.log.info("TEST_SETUP: Build dimm memory deivce list")
        dimm_list = eval(dimm_list % (default_pagesize, default_pagesize))
        for dimm in dimm_list:
            dimm_device = Memory()
            dimm_device.setup_attrs(**dimm)
            dimm_device_list.append(dimm_device)

        test.log.info("TEST_SETUP: Build memory backing config")
        memory_backing_dict = memory_backing_dict.format(default_pagesize)
        memory_backing_dict = eval(memory_backing_dict)

    def attach_dimms(dimm_device_list, plug_option):
        """
        Attach dimm memory devices to vm

        :param dimm_device_list: list of dimm memory device object
        :param plug_option: flagstr for virsh attach-device command
        """
        for dimm_dev in dimm_device_list:
            ret = virsh.attach_device(vm_name, dimm_dev.xml,
                                      flagstr=plug_option, **virsh_args)
            libvirt.check_result(ret)

    def check_dimm_prealloc(exp_prealloc_list):
        """
        Verify dimm memory preallocation status against expected values

        :param exp_prealloc_list: expected dimm memory preallocation list
        """
        preallocated_cmd = params.get("preallocated_cmd")
        preallocated_cmd_protocal = params.get("preallocated_cmd_protocal")
        pattern = params.get("preallocated_pattern")

        ret = virsh.qemu_monitor_command(vm_name, preallocated_cmd,
                                         preallocated_cmd_protocal, debug=True)
        test.log.debug(f"qemu-monitor-command '{preallocated_cmd}' result: {ret.stdout_text}")
        matches = sorted(re.findall(fr'{pattern}', ret.stdout_text, re.DOTALL))
        actual_prealloc_list = [prealloc for _, prealloc in matches]
        if actual_prealloc_list != exp_prealloc_list:
            test.fail(
                f"Expected preallocated list is {exp_prealloc_list}, but found {actual_prealloc_list}")
        nonlocal dimm_name_list
        dimm_name_list = [name for name, _ in matches]

    def check_qemu_object_property(name_list, obj_property, exp_list, exact_match=False):
        """
        Verify qemu object properties against expected values

        :param name_list: list of qemu object names
        :param obj_property: qemu object property name
        :param exp_list: expected values for the property
        :param exact_match: whether to use exact match or substring match
        """
        def _compare_value(exp, act):
            """
            Compare expected value with actual value

            :param exp: expected value
            :param act: actual value
            """
            if exact_match:
                return exp == act
            return exp in act if act else False

        nonlocal qom_cmd_template
        for index, name in enumerate(name_list):
            qom_cmd = qom_cmd_template.format(name, obj_property)
            ret = virsh.qemu_monitor_command(vm_name, qom_cmd, debug=True)
            data_dict = json.loads(ret.stdout_text)
            exp_dict = exp_list[index]

            if "return" in exp_dict:
                if not _compare_value(exp_dict.get("return"), data_dict["return"]):
                    test.fail(
                        f"Expected dimm {name} {obj_property} is {exp_dict}, but found {data_dict}")
            if "error" in exp_dict:
                act_desc = data_dict.get("error", {}).get("desc", "")
                exp_desc = exp_dict["error"]["desc"]
                if not _compare_value(exp_desc, act_desc):
                    test.fail(
                        f"Expected dimm {name} error msg is {exp_dict}, but found {data_dict}")

    libvirt_version.is_libvirt_feature_supported(params)
    virsh_args = {'debug': True, 'ignore_status': False}
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    hugepage_memory = params.get("hugepage_memory")
    vm_attrs = eval(params.get("vm_attrs"))
    memory_backing_dict = params.get("memory_backing_dict")
    plug_type = params.get("plug_type")
    plug_option = params.get("plug_option")

    dimm_list = params.get("dimm_list")
    dimm_device_list = []
    dimm_name_list = []
    exp_prealloc_list = eval(params.get("exp_prealloc_list"))
    exp_type_list = eval(params.get("exp_type_list"))
    exp_mempath_list = eval(params.get("exp_mempath_list"))
    exp_threads_list = eval(params.get("exp_threads_list", "[]"))
    qom_cmd_template = params.get("qom_cmd_template")
    property_type = params.get("property_type")
    property_mempath = params.get("property_mempath")
    property_threads = params.get("property_threads")
    alloc_mode = params.get("alloc_mode")
    consume_value = int(params.get("consume_value"))

    try:
        setup_test()

        # Test steps:
        # 1. Define the guest
        # 2. Cold-plug dimm devices
        # 3. Start the guest
        # 4: Restart the libvirt deamon
        # 5: Hot-plug dimm devices
        # 6: Check dimm memory backend pre-allocated
        # 7: Check dimm memory backing type
        # 8: Check dimm memory backing path
        # 9: Check dimm memory allocation threads
        # 10: Consume guest memory successfully
        test.log.info("TEST_STEP1: Define the guest")
        vm_attrs.update(memory_backing_dict)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()

        if plug_type == "cold_plug":
            test.log.info("TEST_STEP2: Cold-plug dimm devices")
            attach_dimms(dimm_device_list, plug_option)

        test.log.info("TEST_STEP3: Start guest")
        vm.start()
        dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug(f"Domain xml after start is: {dom_xml}")
        vm.wait_for_login().close()

        test.log.info("TEST_STEP4: Restart the libvirt deamon")
        Libvirtd().restart()

        if plug_type == "hot_plug":
            test.log.info("TEST_STEP5: Hot-plug dimm devices")
            attach_dimms(dimm_device_list, plug_option)

        test.log.info("TEST_STEP6: Check dimm memory backend pre-allocated")
        check_dimm_prealloc(exp_prealloc_list)

        test.log.info("TEST_STEP7: Check dimm memory backing type")
        check_qemu_object_property(dimm_name_list, property_type, exp_type_list, True)

        test.log.info("TEST_STEP8: Check dimm memory backing path")
        check_qemu_object_property(dimm_name_list, property_mempath, exp_mempath_list)

        if alloc_mode in ["immediate_with_threads", "hugepage_nodeset"]:
            test.log.info("TEST_STEP9: Check dimm memory allocation threads")
            check_qemu_object_property(dimm_name_list, property_threads, exp_threads_list, True)

        test.log.info("TEST_STEP10: Consume guest memory successfully")
        session = vm.wait_for_login()
        status, output = libvirt_memory.consume_vm_freememory(session, consume_value)
        if status:
            test.fail(f"Fail to consume guest memory. Error:{output}")
        session.close()

    finally:
        test.log.info("TEST_TEARDOWN: Clean up environment.")
        utils_memory.set_num_huge_pages(0)
        bkxml.sync()
