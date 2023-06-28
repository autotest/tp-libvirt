# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os
import re

from avocado.utils import process

from virttest import libvirt_version
from virttest import test_setup
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_test
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest.staging import utils_memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_version import VersionInterval


def check_supported_version(params, test, vm):
    """
    Check the supported version

    :param params: Dictionary with the test parameters
    :param test: Test object
    :param vm: Vm object
    """
    guest_required_kernel = params.get('guest_required_kernel')
    libvirt_version.is_libvirt_feature_supported(params)
    utils_misc.is_qemu_function_supported(params)

    if not vm.is_alive():
        vm.start()
    vm_session = vm.wait_for_login()
    vm_kerv = vm_session.cmd_output('uname -r').strip().split('-')[0]
    vm_session.close()
    if vm_kerv not in VersionInterval(guest_required_kernel):
        test.cancel("Got guest kernel version:%s, which is not in %s" %
                    (vm_kerv, guest_required_kernel))


def update_init_vm_attrs(params):
    """
    Get init vm attrs which used for set up

    :param params: Dictionary with the test parameters
    :return vm_attrs_init: vm attrs for init domain xml
    """
    set_value = eval(params.get("set_value"))
    vm_attrs = eval(params.get("vm_attrs"))
    numa_mem = int(params.get("numa_mem"))
    numa_num = int(params.get("numa_num"))

    memory_affected = []
    current_mem_affected = []
    for value in set_value:
        if value[get_index(params, "init_seq")] != "":
            memory_affected.append(value[get_index(params, "memory_affected")])
            current_mem_affected.append(
                value[get_index(params, "current_mem_affected")])

    vm_attrs_init = {
        **vm_attrs,
        **{'memory': numa_mem * numa_num + sum(memory_affected),
           'current_mem': numa_mem * numa_num + sum(current_mem_affected)}}

    params.update({"vm_attrs_init": vm_attrs_init})

    return vm_attrs_init


def get_index(params, item):
    """
    Get item index in set_items list

    :param params: Dictionary with the test parameters
    :param item: the item you want to get index.
    """
    set_items = eval(params.get("set_items"))
    return set_items.index(item)


def attach_single_mem(vm_name, mem_dict, remove_devices=False, index=0):
    """
    Attach one memory device

    :param vm_name: vm name
    :param mem_dict: memory device dict
    :param remove_devices: remove all device flag
    :param index: memory index
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    if remove_devices:
        vmxml.remove_all_device_by_type('memory')

    obj = libvirt_vmxml.modify_vm_device(
        vmxml, 'memory', mem_dict, index=index)
    return obj


def attach_all_memory_devices(vm, params, operate_flag):
    """
    Attach devices according to the cfg

    :param vm: vm object
    :param params: Dictionary with the test parameters
    :param operate_flag, the flag of operation
    """
    set_value = eval(params.get("set_value"))

    if not vm.is_alive():
        vm.start()

    mem_list = []
    for value in set_value:
        if value[get_index(params, operate_flag)] != "":
            mem_list.append(value[-1])
    if operate_flag == "init_seq":
        for index, mem in enumerate(mem_list):
            dev_dict = globals()[mem]
            remove = True if index == 0 else False
            attach_single_mem(vm.name, dev_dict, remove_devices=remove,
                              index=index)

    elif operate_flag == "hotplug_seq":
        for mem in mem_list:
            dev_dict = globals()[mem]
            mem_dev = memory.Memory()
            mem_dev.setup_attrs(**dev_dict)
            virsh.attach_device(vm.name, mem_dev.xml, debug=True,
                                ignore_status=False)


def detach_all_memory_devices(vm_name, params, operate_flag):
    """
    Detach all devices according to the cfg

    :param vm_name: vm name
    :param params: Dictionary with the test parameters
    :param operate_flag: the flag of operation
    """
    set_value = eval(params.get("set_value"))

    for index, value in enumerate(set_value):
        if value[get_index(params, operate_flag)] != "":
            dev_dict = globals()[value[-1]]
            mem_xml = memory.Memory()
            mem_xml.setup_attrs(**dev_dict)
            virsh.detach_device(vm_name, mem_xml.xml, debug=True,
                                wait_for_event=True, ignore_status=False)


def do_domain_lifecyle(params, vm, expected_xpath):
    """
    Do lifecycle for guest

    :param params: Dictionary with the test parameters
    :param vm: vm object
    :param expected_xpath: expected xpath
    """
    virsh.suspend(vm.name, ignore_status=False, debug=True)
    virsh.resume(vm.name, ignore_status=False, debug=True)
    vm.wait_for_login().close()
    check_mem_config(vm.name, expected_xpath)

    save_file = params.get("save_file")
    if os.path.exists(save_file):
        os.remove(save_file)
    virsh.save(vm.name, save_file, ignore_status=False, debug=True)
    virsh.restore(save_file, ignore_status=False, debug=True)
    vm.wait_for_login().close()
    check_mem_config(vm.name, expected_xpath)

    virsh.managedsave(vm.name, ignore_status=False, debug=True)
    vm.start()
    vm.wait_for_login().close()
    check_mem_config(vm.name, expected_xpath)


def check_mem_config(vm_name, expect_xpath):
    """
    Check current mem config is correct

    :param vm_name: vm name
    :param expect_xpath: expect xpath
    """
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, expect_xpath)


def get_expected_xpath(params, operation):
    """
    Get expected xpath according to the operation

    :param params: Dictionary with the test parameters
    :param operation: operate step
    :return xpath: expected xpath
    """
    del_head = int(params.get("del_head"))
    del_tail = int(params.get("del_tail"))

    xpath_after_start = params.get("xpath_after_start")
    xpath_after_attached = params.get("xpath_after_attached")
    xpath_after_detached = params.get("xpath_after_detached")

    xpath = ""
    if operation == "after_start":
        xpath = eval(xpath_after_start % (
            params.get("vm_attrs_init")["memory"],
            params.get("vm_attrs_init")["current_mem"]))

    elif operation == "after_attached":
        mem = params.get("vm_attrs_init")["memory"]
        current_mem = params.get("vm_attrs_init")["current_mem"]
        memory_affected, current_mem_affected = get_affected_mem(
            params, "hotplug_seq")

        params.update({"attached_mem": mem + sum(memory_affected)})
        params.update({"attached_current_mem": current_mem + sum(
            current_mem_affected)})

        xpath = eval(xpath_after_attached % (
            params.get("attached_mem"), params.get("attached_current_mem")))

    elif operation == "after_detached":
        mem = params.get("attached_mem")
        current_mem = params.get("attached_current_mem")

        memory_affected, current_mem_affected = get_affected_mem(
            params, "hotunplug_seq")
        xpath = eval(xpath_after_detached % (
            mem + sum(memory_affected),
            current_mem + sum(current_mem_affected)))
        # clean the hot plugged xpath
        del xpath[del_head:del_tail]

    return xpath


def get_affected_mem(params, operation):
    """
    Get affected mem according to the operation

    :param params: Dictionary with the test parameters
    :param operation: operate step
    :return: memory_affected, current_mem_affected: affected mem value
    """
    set_value = eval(params.get("set_value"))

    memory_affected = []
    current_mem_affected = []
    for value in set_value:
        if value[get_index(params, operation)] is not None:
            memory_affected.append(value[get_index(params, "memory_affected")])
            current_mem_affected.append(
                value[get_index(params, "current_mem_affected")])

    return memory_affected, current_mem_affected


def run(test, params, env):
    """
    Verify various memory devices could work together
    """

    def setup_test():
        """
        Prepare init xml
        """
        test.log.info("TEST_SETUP: Set hugepage and guest boot ")
        process.run(truncate_cmd, ignore_status=False, shell=True)
        utils_memory.set_num_huge_pages(int(nr_hugepages))
        result = process.run("cat %s" % kernel_hp_file, ignore_status=True,
                             verbose=False).stdout_text.strip()
        if nr_hugepages != result:
            test.fail("Hugepage set failed, should be %s instead of %s" % (
                nr_hugepages, result))

        vm.start()
        utils_test.update_boot_option(vm, args_removed=kernel_params_remove,
                                      args_added=kernel_params_add,
                                      need_reboot=True)
        test.log.info("TEST_SETUP: Prepare guest original xml")
        vm_attrs_init = update_init_vm_attrs(params)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs_init)
        vmxml.sync()

        attach_all_memory_devices(vm, params, "init_seq")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The init xml is:\n%s", vmxml)

    def run_test():
        """
        Do lifecycle and check xml
        """
        test.log.info("TEST_STEP1: Start vm")
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()

        test.log.info("TEST_STEP2: Check xml")
        pattern = get_expected_xpath(params, operation="after_start")
        check_mem_config(vm.name, pattern)

        test.log.info("TEST_STEP3: Check guest memory value")
        guest_mem = utils_misc.get_mem_info(session)
        session.close()

        test.log.info("TEST_STEP4: Restart service")
        libvirtd = utils_libvirtd.Libvirtd()
        libvirtd.restart()

        test.log.info("TEST_STEP5: Hotplug devices")
        attach_all_memory_devices(vm, params, "hotplug_seq")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The xml after hotplug is:\n%s", vmxml)

        hotplug_current_mem_affected = []
        for value in set_value:
            if value[get_index(params, "hotplug_seq")] != "":
                hotplug_current_mem_affected.append(value[get_index(
                    params, "current_mem_affected")])

        test.log.info("TEST_STEP6: Check guest memory increased "
                      "hot plugged memory.")
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        guest_mem_new = utils_misc.get_mem_info(session)
        session.close()

        if not guest_mem == guest_mem_new - int(
                sum(hotplug_current_mem_affected)):
            test.fail("Guest memory:(%s) is different from new guest memory:"
                      "(%s) - sum hot pluged current memory:(%s)" % (
                          guest_mem, guest_mem_new,
                          sum(hotplug_current_mem_affected)))

        test.log.info("TEST_STEP7: Check xml")
        pattern = get_expected_xpath(params, operation="after_attached")
        check_mem_config(vm.name, pattern)

        test.log.info("TEST_STEP8: Do lifecycle and check xml not change")
        do_domain_lifecyle(params, vm, pattern)

        test.log.info("TEST_STEP9: Detach memory devices")
        detach_all_memory_devices(vm_name, params, "hotunplug_seq")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The xml after detached is:\n%s", vmxml)

        test.log.info("TEST_STEP10: Check xml")
        pattern = get_expected_xpath(params, operation="after_detached")
        check_mem_config(vm.name, pattern)
        for detached in pattern[del_head:del_tail]:
            if re.search(str(detached), str(vmxml)):
                test.fail("%s should not exist in current xml")

        test.log.info("TEST_STEP11: Do lifecycle and check xml not change")
        do_domain_lifecyle(params, vm, pattern)

        test.log.info("TEST_STEP12: Destroy again")
        vm.destroy()

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        save_file = params.get("save_file")
        for file in [save_file, nvdimm_path_1, nvdimm_path_2]:
            if os.path.exists(file):
                os.remove(file)
        bkxml.sync()
        hugepage = test_setup.HugePageConfig(params)
        hugepage.cleanup()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    kernel_params_remove = params.get("kernel_params_remove")
    kernel_params_add = params.get("kernel_params_add")
    truncate_cmd = params.get("truncate_cmd")
    nr_hugepages = params.get("nr_hugepages")
    kernel_hp_file = params.get("kernel_hp_file")
    nvdimm_path_1 = params.get("nvdimm_path_1")
    nvdimm_path_2 = params.get("nvdimm_path_2")

    globals()['dimm_dict_1'] = eval(params.get('dimm_dict_1', '{}'))
    globals()['nvdimm_dict_1'] = eval(params.get('nvdimm_dict_1', '{}'))
    globals()['virtio_mem_dict_1'] = eval(params.get('virtio_mem_dict_1', '{}'))

    globals()['dimm_dict_2'] = eval(params.get('dimm_dict_2', '{}'))
    globals()['nvdimm_dict_2'] = eval(params.get('nvdimm_dict_2', '{}'))
    globals()['virtio_mem_dict_2'] = eval(params.get('virtio_mem_dict_2', '{}'))

    set_value = eval(params.get("set_value"))
    del_head = int(params.get("del_head"))
    del_tail = int(params.get("del_tail"))

    try:
        check_supported_version(params, test, vm)
        setup_test()
        run_test()

    finally:
        teardown_test()
