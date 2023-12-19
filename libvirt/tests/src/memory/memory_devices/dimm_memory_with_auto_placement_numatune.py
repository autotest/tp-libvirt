# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.numa import numa_base


def run(test, params, env):
    """
    1.Define guest with dimm devices and start guest.
    2.Attach dimm memory.
    """

    def setup_test():
        """
        Check available numa nodes num.
        """
        test.log.info("TEST_SETUP: Check available numa nodes num")
        numa_obj = numa_base.NumaTest(vm, params, test)
        numa_obj.check_numa_nodes_availability()

    def run_test():
        """
        Verify dimm memory device works with auto placement numatune.
        """
        test.log.info("TEST_SETUP1: Define guest with dimm memory")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        new_dimm = libvirt_vmxml.create_vm_device_by_type('memory', dimm_dict)
        vmxml.devices = vmxml.devices.append(new_dimm)
        vmxml.sync()

        test.log.info("TEST_STEP2: Start vm")
        vm.start()
        vm.wait_for_login().close()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("Get guest xml is '%s' ", vmxml)

        test.log.info("TEST_SETUP3: Hotplug a dimm device")
        attach_mem = libvirt_vmxml.create_vm_device_by_type('memory', attach_dimm)
        virsh.attach_device(vm.name, attach_mem.xml, debug=True,
                            wait_for_event=True, ignore_status=False)

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
    dimm_dict = eval(params.get('dimm_dict', '{}'))
    attach_dimm = eval(params.get("attach_dimm", '{}'))

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
