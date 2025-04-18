# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os
import re

from avocado.utils import process

from virttest import data_dir
from virttest import test_setup
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_test import libvirt

from provider.memory import memory_base
from provider.numa import numa_base


def run(test, params, env):
    """
    Verify various config of dimm memory device settings take effect
    during the life cycle of guest vm.
    """

    def clean_empty_memory_device_config(mem_device_dict):
        """
        Clean empty config of the memory device

        :param mem_device_dict (dict): memory device config dictionary
        """
        for key in list(mem_device_dict.keys()):
            if not mem_device_dict[key]:
                del mem_device_dict[key]

    def check_dimm_mem_device_xml(xpath_dict):
        """
        Check the dimm memory device config by xpath

        :param xpath_dict (dict): xpath dict to check if the memory config is correct,
                                  like {"alias_name":[xpath1, xpath2]},...}
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug(f"Current guest config xml is:\n{vmxml}")
        memory_devices = vmxml.devices.by_device_tag('memory')
        target_memory_num = 0
        for alias_name, xpath_list in xpath_dict.items():
            for memory_device in memory_devices:
                if alias_name == memory_device.alias.get('name'):
                    target_memory_num = target_memory_num + 1
                    for xpath in xpath_list:
                        libvirt_vmxml.check_guest_xml_by_xpaths(memory_device, xpath)
        if target_memory_num != len(xpath_dict):
            test.fail(
                f"Expected {len(xpath_dict)} dimm mem devices with required alias name, but found {target_memory_num}")

    def check_numa_node_memory_allocation(mem_size, numa_list, page_size):
        """
        Check memory is allocated on target numa list with correct page size

        :param mem_size (int): memory size
        :param numa_list (list): target numa list
        :param page_size (str): page size of the memory
        """
        cmd_output = numa_base.get_host_numa_memory_alloc_info(mem_size)
        actual_numa_list = re.findall(r'N(\d+)=', cmd_output)
        if not set(actual_numa_list).issubset(set(numa_list)):
            test.fail(
                f"Expected numa list {numa_list} doesn't contain actual numa in numa_maps output {cmd_output}")
        if not re.search(fr'kernelpagesize_kB={page_size}', cmd_output):
            test.fail(
                f"Failed to find {page_size}kb memory page size in numa_maps output {cmd_output}")

    def check_case_availability():
        """
        Check whether the case is available
        """
        memory_base.check_mem_page_sizes(test, page_size, default_hp_size)
        memory_base.check_supported_version(params, test, vm)

    def setup_test():
        """
        Setup for the case:
        1. Get host available numa nodes
        2. Change parameters according to available numa nodes
        3. Allocate huge page memory for target host node if needs
        """
        if nodeset_num:
            numatest_obj = numa_base.NumaTest(vm, params, test)
            if 1 == nodeset_num:
                min_memory_size = init_size + plug_size
            elif 2 == nodeset_num:
                min_memory_size = init_size if init_size >= plug_size else plug_size
            numatest_obj.check_numa_nodes_availability(nodeset_num, min_memory_size)
            numa_list = numatest_obj.get_available_numa_nodes(min_memory_size)[:nodeset_num]
            nodeset_str = numa_base.convert_to_string_with_dash(
                ','.join([str(node) for node in numa_list]))
            params["numa_node_list"] = numa_list
            source_dict = params.get("source_dict")
            source_xpath = params.get("source_xpath")
            source_dict = eval(source_dict % nodeset_str)
            source_xpath = eval(source_xpath % nodeset_str)
            init_mem_device_dict["source"] = source_dict
            plug_mem_device_dict["source"] = source_dict
            init_xpath_list[0] = source_xpath
            plug_xpath_list[0] = source_xpath
            test.log.debug(f"Selected numa nodeset is:{nodeset_str}")

            if use_huge_page:
                params["target_nodes"] = " ".join([str(node) for node in numa_list])
                params["target_hugepages"] = (init_size + plug_size) / default_hp_size
                hpc = test_setup.HugePageConfig(params)
                hpc.setup()

        if vm.is_alive():
            vm.destroy()

    def run_test():
        """
        Test steps
        """
        test.log.info("TEST_STEP1: Define the guest")
        clean_empty_memory_device_config(init_mem_device_dict)
        memory_base.define_guest_with_memory_device(params, init_mem_device_dict, vm_attrs)

        if use_huge_page:
            test.log.info("TEST_STEP2: Check secure context for default huge page mount path")
            cmd_result = process.run(check_path_secure_cmd, ignore_status=True, shell=True)
            libvirt.check_result(cmd_result, expected_match=path_secure_context)

        test.log.info("TEST_STEP3: Start the guest")
        vm.start()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug(f"Guest config xml after start is:\n{vmxml}")

        if use_huge_page:
            test.log.info("TEST_STEP4: Check secure context for default huge page mount path")
            check_cmd = check_guest_secure_cmd % (vm.get_id(), vm_name)
            cmd_result = process.run(check_cmd, ignore_status=True, shell=True)
            libvirt.check_result(cmd_result, expected_match=guest_secure_context)

        test.log.info("TEST_STEP5: Check dimm memory device config by virsh dumpxml")
        check_dimm_mem_device_xml({init_alias_name: init_xpath_list})

        if bios_check:
            test.log.info("TEST_STEP6: Check smbios, sysinfo and idmap info by virsh dumpxml")
            libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, bios_check_xpath)

        test.log.info("TEST_STEP7: Hotplug a dimm memory device")
        clean_empty_memory_device_config(plug_mem_device_dict)
        mem_device = memory.Memory()
        mem_device.setup_attrs(**plug_mem_device_dict)
        virsh.attach_device(vm_name, mem_device.xml, **virsh_dargs)

        test.log.info(
            "TEST_STEP8: Check dimm memory device config by virsh dumpxml")
        check_dimm_mem_device_xml(
            {init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        test.log.info("TEST_STEP9: Consume the guest memory")
        with vm.wait_for_login() as session:
            libvirt_memory.consume_vm_freememory(session)

        if nodeset_num:
            test.log.info("TEST_STEP10: Check dimm memory device source node")
            numa_list = [str(node) for node in params['numa_node_list']]
            expected_page_size = default_hp_size if use_huge_page else page_size
            check_numa_node_memory_allocation(init_size, numa_list, expected_page_size)
            check_numa_node_memory_allocation(plug_size, numa_list, expected_page_size)

        test.log.info("TEST_STEP11: Life cycle test")
        virsh.suspend(vm_name, **virsh_dargs)
        virsh.resume(vm_name, **virsh_dargs)
        check_dimm_mem_device_xml(
            {init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        virsh.save(vm_name, state_file, **virsh_dargs)
        virsh.restore(state_file, **virsh_dargs)
        check_dimm_mem_device_xml(
            {init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        virsh.managedsave(vm_name, **virsh_dargs)
        vm.start()
        check_dimm_mem_device_xml(
            {init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        vm.reboot()
        vm.wait_for_login().close()
        check_dimm_mem_device_xml(
            {init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        libvirt_daemon = Libvirtd()
        if not libvirt_daemon.restart(reset_failed=False):
            test.error("libvirt deamon restarts failed or is not working properly")
        check_dimm_mem_device_xml(
            {init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

    def teardown_test():
        """
        1. Restore guest config xml
        2. Clean huge page memory
        3. Remove state file
        """
        bkxml.sync()
        if use_huge_page:
            hpc = test_setup.HugePageConfig(params)
            hpc.cleanup()
        if os.path.exists(state_file):
            os.remove(state_file)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    vm_attrs = eval(params.get("vm_attrs", "{}"))
    bios_check = params.get("bios_check", "no") == "yes"
    if bios_check:
        sysinfo_attrs = eval(params.get("sysinfo_attrs", "{}"))
        idmap_attrs = eval(params.get("idmap_attrs", "{}"))
        os_attrs = eval(params.get("os_attrs", "{}"))
        vm_attrs.update({'sysinfo': sysinfo_attrs,
                        'os': os_attrs, 'idmap': idmap_attrs})
        bios_check_xpath = eval(params.get("bios_check_xpath"))
    init_mem_device_dict = eval(
        params.get("init_mem_device_dict", "{}"))
    plug_mem_device_dict = eval(
        params.get("plug_mem_device_dict", "{}"))
    init_xpath_list = eval(params.get("init_xpath_list"))
    plug_xpath_list = eval(params.get("plug_xpath_list"))
    check_path_secure_cmd = params.get("check_path_secure_cmd")
    path_secure_context = params.get("path_secure_context")
    check_guest_secure_cmd = params.get("check_guest_secure_cmd")
    guest_secure_context = params.get("guest_secure_context")
    init_alias_name = params.get("init_alias_name")
    plug_alias_name = params.get("plug_alias_name")
    page_size = int(params.get("page_size"))
    default_hp_size = int(params.get("default_hp_size"))
    init_size = int(params.get("init_size"))
    plug_size = int(params.get("plug_size"))
    nodeset_num = int(params.get("nodeset_num", "0"))
    use_huge_page = "yes" == params.get("use_huge_page")
    state_file = f"{data_dir.get_tmp_dir()}/{vm_name}.save"
    virsh_dargs = {"debug": True, "ignore_status": False}

    try:
        check_case_availability()
        setup_test()
        run_test()

    finally:
        teardown_test()
