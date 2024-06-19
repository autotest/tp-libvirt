# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import re

from avocado.utils import memory

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.staging import utils_memory

from provider.memory import memory_base


def get_vm_attrs(test, params):
    """
    Get vm attrs.

    :param test: Virt test object.
    :param params: Dict with test params.
    :return vm_attrs: expected vm attrs dict.
    """
    source_attr = params.get('source_attr')
    hugepages_attr = params.get('hugepages_attr')
    vm_attrs = eval(params.get("vm_attrs"))

    mb_value = ""
    for item in [source_attr, hugepages_attr]:
        if item is not None:
            mb_value = mb_value + item + ","
    mb = eval("{'mb':{%s}}" % mb_value[:-1])
    vm_attrs.update(mb)
    test.log.debug("Get current vm attrs is :%s", vm_attrs)

    return vm_attrs


def run(test, params, env):
    """
    Verify dynamic memory slots attribute works with virtio-mem memory device
    """
    def setup_test():
        """
        Allocate huge page memory
        """
        test.log.info("TEST_SETUP: Allocate huge page memory")
        if memory_backing == "hugepages_mb":
            utils_memory.set_num_huge_pages(allocate_size/default_hugepage_size)

    def run_test():
        """
        Verify dynamic memory slots attribute works with virtio-mem memory device
        """
        test.log.info("TEST_SETUP1: Define guest with virtio memory")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vm_attrs = get_vm_attrs(test, params)
        vmxml.setup_attrs(**vm_attrs)
        mem_obj = libvirt_vmxml.create_vm_device_by_type("memory", virtio_dict)
        vmxml.devices = vmxml.devices.append(mem_obj)
        vmxml.sync()

        test.log.info("TEST_STEP2: Start vm")
        vm.start()
        vm.wait_for_login().close()
        test.log.debug("Get xml after starting vm:%s",
                       vm_xml.VMXML.new_from_inactive_dumpxml(vm_name))

        test.log.info("TEST_SETUP3: Hotplug a virtio-mem device")
        virsh.attach_device(
            vm.name, mem_obj.xml, wait_for_event=True, **virsh_dargs)

        test.log.info("TEST_SETUP4: Check qemu-monitor-command")
        output = virsh.qemu_monitor_command(
            vm_name, monitor_cmd, monitor_option, **virsh_dargs
        )
        for pattern in slots_pattern:
            if not re.search(pattern, output.stdout_text.strip()):
                test.fail("Expect '%s' in qemu-monitor-command" % pattern)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        utils_memory.set_num_huge_pages(0)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    actual_machine_version = ''.join(
        re.findall(r'\d+', vmxml.os.machine.split('-')[-1]))
    virsh_dargs = {"debug": True, "ignore_status": False}

    default_hugepage_size = memory.get_huge_page_size()
    allocate_size = int(params.get("allocate_size"))
    memory_backing = params.get("memory_backing")
    machine_version = params.get("machine_version")
    monitor_cmd = params.get("monitor_cmd")
    monitor_option = params.get("monitor_option")
    slots_pattern = eval(params.get("slots_pattern"))
    dynamic_slot_attr = eval(params.get("dynamic_slot_attr", "{}"))
    virtio_dict = eval(params.get('virtio_dict') % default_hugepage_size)
    if dynamic_slot_attr:
        virtio_dict['target']['attrs'] = dynamic_slot_attr

    memory_base.check_supported_version(params, test, vm)
    if actual_machine_version < machine_version:
        test.cancel("Guest config machine should be >= pc-q35-rhel9.4.0")
    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
