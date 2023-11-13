# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import time

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import memory
from virttest.utils_libvirt import libvirt_vmxml

from provider.memory import memory_base
from provider.numa import numa_base


def run(test, params, env):
    """
    1.Define guest with dimm devices.
    2.Attach dimm and check mem value
    """

    def setup_test():
        """
        Check available numa nodes num.
        """
        memory_base.check_supported_version(params, test, vm)
        test.log.info("TEST_SETUP: Check available numa nodes num")
        numa_obj = numa_base.NumaTest(vm, params, test)
        numa_obj.check_numa_nodes_availability()

    def run_test():
        """
        Define guest, check xml, check memory
        """
        test.log.info("TEST_SETUP1: Define guest")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        virsh.define(vmxml.xml, debug=True, ignore_status=False)

        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_inactive_dumpxml(vm_name), 'memory',
            virtio_mem_dict)

        test.log.info("TEST_STEP2: Start vm")
        vm.start()
        vm.wait_for_login().close()

        test.log.info("TEST_SETUP3: Hotplug a virtio-mem device")
        mem_obj = memory.Memory()
        mem_obj.setup_attrs(**virtio_mem_dict)
        virsh.attach_device(vm.name, mem_obj.xml, debug=True,
                            wait_for_event=True, ignore_status=False)

        test.log.info("TEST_SETUP4: Check the virtio-mem device memory")
        time.sleep(3)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("The new xml is:\n%s", vmxml)

        mem_list = vmxml.devices.by_device_tag('memory')
        for mem in mem_list:
            actual_size = mem.fetch_attrs()['target']['current_size']
            if str(actual_size) != requested_size:
                test.fail("Expected to get requested size '%s', "
                          "but got '%s'" % (requested_size, actual_size))
            else:
                test.log.debug('Check requested size "%s" correctly' % requested_size)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm_attrs = eval(params.get('vm_attrs', '{}'))
    virtio_mem_dict = eval(params.get('virtio_mem_dict', '{}'))
    requested_size = params.get("requested_size")

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
