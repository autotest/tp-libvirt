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
import os
import re

from avocado.utils import memory as avocado_mem
from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.staging import utils_memory
from virttest.utils_libvirtd import Libvirtd

from provider.memory import memory_base


def run(test, params, env):
    """
    Verify nvdimm memory device works with various memory backing type
    """
    def setup_test():
        """
        Test setup:
        1. Set up hugepage
        2. Build nvdimm memory device list
        3. Build memory backing config
        """
        def is_in_range(num, range_str):
            """
            Check if the number is in the given range string

            :param num: int, target number
            :param range_str: string, range string to check
            :return: bool, True if number is in range string, False otherwise
            """
            if '-' in range_str:
                start, end = map(int, range_str.split('-'))
                return start <= num <= end
            return num == int(range_str) if range_str else False

        nonlocal nvdimm_path_list, memory_backing_dict
        test.log.info("TEST_SETUP: Set up hugepage")
        default_pagesize = avocado_mem.get_huge_page_size()
        hp_num = int(hugepage_memory) // default_pagesize
        utils_memory.set_num_huge_pages(hp_num)

        test.log.info("TEST_SETUP: Build nvdimm list")
        init_nvdimms_id_range = params.get("init_nvdimms_id_range")
        hotplug_nvdimms_id_range = params.get("hotplug_nvdimms_id_range", "")
        nvdimm_path = params.get("nvdimm_path")
        nvdimm_source_map = eval(params.get("nvdimm_source_map").format(default_pagesize))
        nvdimm_node_map = eval(params.get("nvdimm_node_map"))
        nvdimm_dict_str = params.get("nvdimm_dict")
        for i in range(nvdimm_num):
            n_path = nvdimm_path.format(i)
            n_source = nvdimm_source_map.get(i, "").format(default_pagesize)
            n_node = nvdimm_node_map.get(i)

            nvdimm_path_list.append(n_path)
            process.run(f"truncate -s {nvdimm_size}k {n_path}", verbose=True)

            nvdimm_dict = eval(nvdimm_dict_str.format(n_path, n_source, n_node))
            if is_in_range(i, init_nvdimms_id_range):
                init_nvdimms.append(nvdimm_dict)
            if is_in_range(i, hotplug_nvdimms_id_range):
                hotplug_nvdimms.append(nvdimm_dict)

        test.log.info("TEST_SETUP: Build memory backing config")
        memory_backing_dict = params.get("memory_backing_dict").format(default_pagesize)
        memory_backing_dict = eval(memory_backing_dict)

    def check_nvdimm_prealloc(exp_prealloc_list):
        """
        Verify nvdimm memory preallocation status against expected values

        :param exp_prealloc_list: expected nvdimm memory preallocation list
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
        nonlocal nvdimm_name_list
        nvdimm_name_list = [name for name, _ in matches]

    def check_qemu_object_property(name_list, obj_property, exp_list):
        """
        Verify qemu object properties against expected values

        :param name_list: list of qemu object names
        :param obj_property: qemu object property name
        :param exp_list: expected values for the property
        """
        qom_cmd_template = params.get("qom_cmd_template")
        for index, name in enumerate(name_list):
            qom_cmd = qom_cmd_template.format(name, obj_property)
            ret = virsh.qemu_monitor_command(vm_name, qom_cmd, debug=True)
            data_dict = json.loads(ret.stdout_text)
            exp_value = exp_list[index]

            if "return" not in data_dict:
                test.fail(f"QOM command {qom_cmd} doesn't have return value: {ret.stdout_text}")
            if exp_value != data_dict["return"]:
                test.fail(
                    f"Expected nvdimm {name} {obj_property} is {exp_value}, but found {data_dict['return']}")

    def run_test():
        """
        Test steps:
        1. Define the guest
        2. Start the guest
        3: Restart the libvirt deamon
        4: Hot-plug nvdimm devices
        5: Check nvdimm memory backend pre-allocated
        6: Check nvdimm memory backing type
        7: Check nvdimm memory backing path
        8: Check nvdimm memory allocation threads
        9: Login the guest and create file on each nvdimm device
        """
        test.log.info("TEST_STEP1: Define the guest")
        vm_attrs.update(memory_backing_dict)
        memory_base.define_guest_with_memory_device(params, init_nvdimms, vm_attrs)

        test.log.info("TEST_STEP2: Start guest")
        vm.start()

        test.log.info("TEST_STEP3: Restart the libvirt deamon")
        Libvirtd().restart()

        if hotplug_nvdimms:
            test.log.info("TEST_STEP4: Hot-plug nvdimm devices")
            for nvdimm in hotplug_nvdimms:
                nvdimm_xml_dev = Memory()
                nvdimm_xml_dev.setup_attrs(**nvdimm)
                virsh.attach_device(vm_name, nvdimm_xml_dev.xml, **virsh_args)

        test.log.info("TEST_STEP5: Check nvdimm memory backend pre-allocated")
        check_nvdimm_prealloc([exp_prealloc] * nvdimm_num)

        test.log.info("TEST_STEP6: Check nvdimm memory backing type")
        check_qemu_object_property(nvdimm_name_list, property_type, [exp_type] * nvdimm_num)

        test.log.info("TEST_STEP7: Check nvdimm memory backing path")
        check_qemu_object_property(nvdimm_name_list, property_mempath, nvdimm_path_list)

        if alloc_mode in ["immediate_with_threads", "hugepage_nodeset"]:
            test.log.info("TEST_STEP8: Check nvdimm memory allocation threads")
            check_qemu_object_property(nvdimm_name_list, property_threads, [
                                       int(threads)] * nvdimm_num)

        test.log.info("TEST_STEP9: Login the guest and create file on each nvdimm device")
        with vm.wait_for_login() as session:
            for i in range(nvdimm_num):
                memory_base.create_file_within_nvdimm_disk(
                    test, session, test_device=nvdimm_device.format(i),
                    mount_point=nvdimm_mount_point.format(i), test_file=nvdimm_file.format(i, i),
                    test_str=file_content)

    def teardown_test():
        """
        Clean up environment after test
        1. Remove hugepage
        2. Restore domain xml
        3. Remove nvdimm backing files
        """
        utils_memory.set_num_huge_pages(0)
        bkxml.sync()
        for n_path in nvdimm_path_list:
            if os.path.exists(n_path):
                os.remove(n_path)

    libvirt_version.is_libvirt_feature_supported(params)
    virsh_args = {'debug': True, 'ignore_status': False}
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    nvdimm_path_list = []
    memory_backing_dict = {}
    nvdimm_name_list = []
    init_nvdimms = []
    hotplug_nvdimms = []
    hugepage_memory = params.get("hugepage_memory")
    vm_attrs = eval(params.get("vm_attrs"))
    memory_backing_dict = params.get("memory_backing_dict")
    nvdimm_size = params.get("nvdimm_size")
    nvdimm_num = int(params.get("nvdimm_num"))
    exp_type = params.get("exp_type")
    exp_prealloc = params.get("exp_prealloc")
    threads = params.get("threads")
    nvdimm_device = params.get("nvdimm_device")
    nvdimm_mount_point = params.get("nvdimm_mount_point")
    nvdimm_file = params.get("nvdimm_file")
    file_content = params.get("file_content")

    property_type = params.get("property_type")
    property_mempath = params.get("property_mempath")
    property_threads = params.get("property_threads")
    alloc_mode = params.get("alloc_mode")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
