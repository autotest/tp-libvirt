# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import os

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from provider.memory import memory_base


def define_guest(test, params):
    """
    Define guest.

    :param test: test object.
    :param params: dict, test parameters.
    """
    vm_name = params.get("main_vm")
    vm_attrs = eval(params.get("vm_attrs"))
    nvdimm_dict = eval(params.get("nvdimm_dict"))
    invalid_setting = params.get("invalid_setting")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml.setup_attrs(**vm_attrs)
    mem_obj = memory_base.prepare_mem_obj(nvdimm_dict)
    vmxml.devices = vmxml.devices.append(mem_obj)
    test.log.debug("Define vm with %s." % vmxml)

    # Check libvirt version
    if libvirt_version.version_compare(9, 0, 0) and \
            invalid_setting == "unexisted_node":
        define_error = params.get("start_vm_error")
    elif not libvirt_version.version_compare(9, 9, 0) and \
            invalid_setting == "invalid_addr_type":
        define_error = params.get("define_error_8")
    else:
        define_error = params.get("define_error")

    # Redefine define_error for checking start_vm_error
    params.update({"define_error": define_error})

    try:
        vmxml.sync()
    except Exception as e:
        if define_error:
            if define_error not in str(e):
                test.fail("Expect to get '%s' error, but got '%s'" % (define_error, e))
        else:
            test.fail("Expect define successfully, but failed with '%s'" % e)


def run(test, params, env):
    """
    Verify error messages prompt with invalid nvdimm memory device configs

    1.invalid value:
     exceed slot number, max address base, nonexistent guest node
     nonexistent path, invalid pagesize, invalid address type, small label size
     label size bigger than memory size, :memory size bigger than backing file size
     discard.
    2.memory setting: with numa
    """

    def setup_test():
        """
        Create file backend for nvdimm device.
        """
        test.log.info("Setup env.")
        if invalid_setting != "unexisted_path":
            process.run('truncate -s %s %s' % (nvdimm_file_size, nvdimm_path),
                        verbose=True, shell=True)

    def run_test():
        """
        Define vm with nvdimm and Start vm.
        Hotplug nvdimm.
        """
        test.log.info("TEST_STEP1: Define vm and check result")
        define_guest(test, params)

        test.log.info("TEST_STEP2: Start guest ")
        start_result = virsh.start(vm_name, debug=True, ignore_status=True)
        if start_vm_error:
            libvirt.check_result(start_result, start_vm_error)
        else:
            libvirt.check_exit_status(start_result)

        test.log.info("TEST_STEP3: Define guest without nvdimm devices")
        original_xml.setup_attrs(**vm_attrs)
        test.log.debug("Define vm without nvdimm by '%s' \n", original_xml)
        original_xml.sync()
        virsh.start(vm_name, debug=True, ignore_status=False)
        vm.wait_for_login().close()

        test.log.info("TEST_STEP4: Hotplug nvdimm memory device")
        mem_obj = memory_base.prepare_mem_obj(nvdimm_dict)
        result = virsh.attach_device(vm_name, mem_obj.xml, debug=True).stderr_text
        if attach_error not in result:
            test.fail("Expected get error '%s', but got '%s'" % (attach_error, result))

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        bkxml.sync()
        if os.path.exists(nvdimm_path):
            os.remove(nvdimm_path)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    original_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = original_xml.copy()
    invalid_setting = params.get("invalid_setting")
    nvdimm_file_size = params.get("nvdimm_file_size")
    nvdimm_path = params.get("nvdimm_path")
    nvdimm_dict = eval(params.get("nvdimm_dict"))
    vm_attrs = eval(params.get("vm_attrs"))
    # Get start vm error
    if libvirt_version.version_compare(9, 0, 0) and \
            invalid_setting == "unexisted_node":
        start_vm_error = ""
    else:
        start_vm_error = params.get("start_vm_error")

    # Get attach error
    if invalid_setting == "max_addr":
        attach_error = params.get('attach_error')
    elif not libvirt_version.version_compare(9, 9, 0) and \
            invalid_setting == "invalid_addr_type":
        attach_error = params.get('attach_error_8')
    else:
        attach_error = params.get("start_vm_error", params.get("define_error"))

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
