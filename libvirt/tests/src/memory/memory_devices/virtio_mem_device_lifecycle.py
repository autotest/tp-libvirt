#
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

from virttest.utils_libvirtd import Libvirtd
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest import test_setup
from virttest import utils_misc
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_vmxml

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

    def check_virtio_mem_device_xml(xpath_dict):
        """
        Check the virtio-mem memory device config by xpath

        :param xpath_dict (dict): xpath dict to check if the memory config is correct,
                                  like {"alias_name":[xpath1, xpath2]},...}
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Current guest config xml is:\n%s", vmxml)
        memory_devices = vmxml.devices.by_device_tag('memory')
        target_memory_num = 0
        for alias_name, xpath_list in xpath_dict.items():
            for memory_device in memory_devices:
                if alias_name == memory_device.alias.get('name'):
                    target_memory_num = target_memory_num + 1
                    for xpath in xpath_list:
                        libvirt_vmxml.check_guest_xml_by_xpaths(memory_device, xpath)
        if target_memory_num != len(xpath_dict):
            test.fail('Expected %d virtio-mem mem devices with required alias name, but found %d'
                      % (len(xpath_dict), target_memory_num))

    def check_current_mem_size(alias_name, expect_current_size):
        """
        Check if virtio-mem memory with alias_name has expected current memory size

        :param alias_name (str): alias name of the virtio-mem device
        :param expect_current_size (int): expected current memory size of the virtio-mem device

        :return: bool, true if virtio-mem memory with alias_name has expected current memory size
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        memory_devices = vmxml.devices.by_device_tag('memory')
        for memory_device in memory_devices:
            if alias_name == memory_device.alias.get('name') and memory_device.target.current_size == expect_current_size:
                return True
        return False

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
            source_dict = params.get("source_dict")
            source_xpath = params.get("source_xpath")
            source_dict = eval(source_dict % nodeset_str)
            source_xpath = eval(source_xpath % nodeset_str)
            init_mem_device_dict["source"] = source_dict
            plug_mem_device_dict["source"] = source_dict
            init_xpath_list[0] = source_xpath
            plug_xpath_list[0] = source_xpath
            test.log.debug("Selected numa nodeset is:%s", nodeset_str)

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

        test.log.info("TEST_STEP2: Start the guest")
        vm.start()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Guest config xml after start is:\n%s", vmxml)

        test.log.info("TEST_STEP3: Wait for the guest os is boot up,"
                      "Check virtio-mem memory device config by virsh dumpxml")
        vm.wait_for_login().close()
        check_virtio_mem_device_xml({init_alias_name: init_xpath_list})

        test.log.info("TEST_STEP4: Hotplug a virtio-mem memory device")
        clean_empty_memory_device_config(plug_mem_device_dict)
        mem_device = memory.Memory()
        mem_device.setup_attrs(**plug_mem_device_dict)
        virsh.attach_device(vm_name, mem_device.xml, debug=True, ignore_status=False)
        if not utils_misc.wait_for(
                lambda: check_current_mem_size(plug_alias_name, plug_requested), 20):
            test.fail('Hot-plugged virtio-mem mem devices with alias name %s should have '
                      'current memory size %d' % (plug_alias_name, plug_requested))

        test.log.info("TEST_STEP5: Consume the guest memory")
        session = vm.wait_for_login()
        libvirt_memory.consume_vm_freememory(session)

        test.log.info(
            "TEST_STEP6: Check virtio-mem memory device config by virsh dumpxml")
        check_virtio_mem_device_xml({init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        test.log.info("TEST_STEP7: Life cycle test")
        virsh.suspend(vm_name, ignore_status=False, debug=True)
        virsh.resume(vm_name, ignore_status=False, debug=True)
        check_virtio_mem_device_xml({init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        virsh.save(vm_name, state_file, ignore_status=False, debug=True)
        virsh.restore(state_file, ignore_status=False, debug=True)
        check_virtio_mem_device_xml({init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        virsh.managedsave(vm_name, ignore_status=False, debug=True)
        vm.start()
        check_virtio_mem_device_xml({init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        vm.reboot()
        vm.wait_for_login().close()
        check_virtio_mem_device_xml({init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

        libvirt_daemon = Libvirtd()
        if not libvirt_daemon.restart(reset_failed=False):
            test.fail('libvirt deamon restarts failed or is not working properly')
        check_virtio_mem_device_xml({init_alias_name: init_xpath_list, plug_alias_name: plug_xpath_list})

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
    init_mem_device_dict = eval(
        params.get("init_mem_device_dict", "{}"))
    plug_mem_device_dict = eval(
        params.get("plug_mem_device_dict", "{}"))
    init_xpath_list = eval(params.get("init_xpath_list"))
    plug_xpath_list = eval(params.get("plug_xpath_list"))
    init_alias_name = params.get("init_alias_name")
    plug_alias_name = params.get("plug_alias_name")
    plug_requested = int(params.get("plug_requested"))
    page_size = int(params.get("page_size"))
    default_hp_size = int(params.get("default_hp_size"))
    init_size = int(params.get("init_size"))
    plug_size = int(params.get("plug_size"))
    nodeset_num = int(params.get("nodeset_num", "0"))
    use_huge_page = "yes" == params.get("use_huge_page")
    state_file = params.get('state_file', '/tmp/%s.save') % vm_name

    try:
        check_case_availability()
        setup_test()
        run_test()

    finally:
        teardown_test()
