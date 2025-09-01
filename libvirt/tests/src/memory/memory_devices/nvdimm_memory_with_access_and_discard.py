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

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.utils_libvirtd import Libvirtd

from provider.memory import memory_base


def run(test, params, env):
    """
    Verify nvdimm memory device behaviors with access and discard settings
    """
    def setup_test():
        """
        Construct nvdimms' variables and create nvdimms' backing files
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

        for i in range(nvdimm_num):
            n_path = nvdimm_path_prefix.format(i)
            n_access = nvdimm_dynamic_access.get(i, "")
            n_node = nvdimm_dynamic_node.get(i)

            nvdimms_path_list.append(n_path)
            process.run(f"truncate -s {nvdimm_size}k {n_path}", verbose=True)

            nvdimm_dict = eval(nvdimm_dict_template.format(n_access, n_path, n_node))
            if is_in_range(i, init_nvdimms_id_range):
                init_nvdimms.append(nvdimm_dict)
            if is_in_range(i, hotplug_nvdimms_id_range):
                hotplug_nvdimms.append(nvdimm_dict)

    def check_nvdimm_share(exp_share_list):
        """
        Verify nvdimm memory share status against expected values

        :param exp_share_list: expected nvdimm memory share list
        """
        share_cmd = params.get("share_cmd")
        share_cmd_protocal = params.get("share_cmd_protocal")
        pattern = params.get("share_pattern")

        ret = virsh.qemu_monitor_command(vm_name, share_cmd,
                                         share_cmd_protocal, debug=True)
        test.log.debug(f"qemu-monitor-command '{share_cmd}' result: {ret.stdout_text}")
        matches = sorted(re.findall(fr'{pattern}', ret.stdout_text, re.DOTALL))
        actual_share_list = [share for _, share in matches]
        if actual_share_list != exp_share_list:
            test.fail(
                f"Expected share list is {exp_share_list}, but found {actual_share_list}")

        nonlocal nvdimm_name_list
        nvdimm_name_list = [name for name, _ in matches]

    def check_qemu_object_discard(name_list, exp_discard_list):
        """
        Verify qemu object dicard property against expected values

        :param name_list: list of qemu object names
        :param exp_list: expected list for discard property
        """
        discard_qom_cmd = params.get("discard_qom_cmd")
        for index, name in enumerate(name_list):
            qom_cmd = discard_qom_cmd.format(name)
            ret = virsh.qemu_monitor_command(vm_name, qom_cmd, debug=True)
            data_dict = json.loads(ret.stdout_text)
            exp_value = exp_discard_list[index]

            if "return" not in data_dict:
                test.fail(f"QOM command {qom_cmd} doesn't have return value: {ret.stdout_text}")

            if data_dict["return"] != exp_value:
                test.fail(
                    f"Expected nvdimm discard is {exp_value}, but found {data_dict['return']}")

    def run_test():
        """
        Test steps:
        1. Define the guest
        2. Start guest
        3. Restart libvirt daemon
        4: Hot plug nvdimms
        5: Check nvdimm memory device share access setting
        6: Check the all nvdimm memory device discard setting
        """
        test.log.info("TEST_STEP1: Define the guest")
        memory_base.define_guest_with_memory_device(params, init_nvdimms, vm_attrs)

        test.log.info("TEST_STEP2: Start guest")
        vm.start()

        test.log.info("TEST_STEP3: Restart libvirt daemon")
        Libvirtd().restart()

        if hotplug_nvdimms:
            test.log.info("TEST_STEP4: Hot plug nvdimms")
            for nvdimm in hotplug_nvdimms:
                nvdimm_device = Memory()
                nvdimm_device.setup_attrs(**nvdimm)
                virsh.attach_device(vm_name, nvdimm_device.xml, **virsh_args)

        test.log.info("TEST_STEP5: Check nvdimm memory device share access setting")
        check_nvdimm_share(exp_share_list)

        test.log.info("TEST_STEP6: Check the all nvdimm memory device discard setting")
        check_qemu_object_discard(nvdimm_name_list, exp_discard_list)

    def teardown_test():
        """
        Clean up environment after test
        1. Remove nvdimm memory backing files
        2. Restore domain xml
        """
        for n_path in nvdimms_path_list:
            if os.path.exists(n_path):
                os.remove(n_path)
        bkxml.sync()

    virsh_args = {'debug': True, 'ignore_status': False}
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    nvdimms_path_list = []
    nvdimm_name_list = []
    init_nvdimms = []
    hotplug_nvdimms = []
    nvdimm_size = int(params.get("nvdimm_size"))
    vm_attrs = eval(params.get("vm_attrs"))
    nvdimm_num = int(params.get("nvdimm_num"))
    nvdimm_path_prefix = params.get("nvdimm_path_prefix")
    nvdimm_dict_template = params.get("nvdimm_dict_template")
    nvdimm_dynamic_access = eval(params.get("nvdimm_dynamic_access"))
    nvdimm_dynamic_node = eval(params.get("nvdimm_dynamic_node"))
    init_nvdimms_id_range = params.get("init_nvdimms_id_range", "")
    hotplug_nvdimms_id_range = params.get("hotplug_nvdimms_id_range", "")
    exp_share_list = eval(params.get("exp_share_list"))
    exp_discard_list = eval(params.get("exp_discard_list"))

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
