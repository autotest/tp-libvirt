# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liang Cong <lcong@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Verify nvdimm memory device works with various memory allocation
    and guest numa settings
    """

    def setup_test():
        """
        Set up nvdimm backend file
        """
        process.run(f"truncate -s {nvdimm_size}K {nvdimm_path}", verbose=True)

    def run_test():
        """
        Test steps:
        1. Define vm
        2. Start the vm / cold plug nvdimm
        3. Check domain xml / hot plug nvdimm
        """
        test.log.info("TEST_STEP1: Define vm")
        vmxml.setup_attrs(**vm_attrs)
        if device_operation == "define_with_nvdimm":
            nvdimm_obj = libvirt_vmxml.create_vm_device_by_type('memory', nvdimm_dict)
            vmxml.devices = vmxml.devices.append(nvdimm_obj)
        res = virsh.define(vmxml.xml, debug=True)
        libvirt.check_result(res, define_error)

        if not define_error:
            if device_operation == "cold_plug_nvdimm":
                test.log.info("TEST_STEP2: Cold plug nvdimm")
                res = virsh.attach_device(vm_name, nvdimm_xml.xml, flagstr=plug_option)
                libvirt.check_result(res, cold_plug_error)
            else:
                test.log.info("TEST_STEP2: Start vm")
                vm.start()

                if device_operation == "define_with_nvdimm":
                    test.log.info("TEST_STEP3: check the domain config by virsh dumpxml")
                    nonlocal domain_xpath
                    curr_mem = numa_mem = mem_value - nvdimm_size
                    domain_xpath = eval(domain_xpath.format(numa_mem, curr_mem))
                    dom_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                    libvirt_vmxml.check_guest_xml_by_xpaths(dom_xml, domain_xpath)

                if device_operation == "hot_plug_nvdimm":
                    test.log.info("TEST_STEP3: Hot plug nvdimm")
                    res = virsh.attach_device(vm_name, nvdimm_xml.xml, flagstr=plug_option)
                    libvirt.check_result(res, hot_plug_error)

    def teardown_test():
        """
        Clean up environment after test
        1. restore the xml config of domain
        2. remove the nvdimm file
        """
        bkxml.sync()
        if os.path.exists(nvdimm_path):
            os.remove(nvdimm_path)

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    device_operation = params.get("device_operation")
    vm_attrs = eval(params.get("vm_attrs"))
    nvdimm_dict = eval(params.get("nvdimm_dict"))
    define_error = params.get("define_error")
    cold_plug_error = params.get("cold_plug_error")
    hot_plug_error = params.get("hot_plug_error")
    device_operation = params.get("device_operation")
    plug_option = params.get("plug_option")
    domain_xpath = params.get("domain_xpath")
    mem_value = int(params.get("mem_value"))
    nvdimm_size = int(params.get("nvdimm_size"))
    nvdimm_path = params.get("nvdimm_path")
    nvdimm_xml = Memory()
    nvdimm_xml.setup_attrs(**nvdimm_dict)

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
