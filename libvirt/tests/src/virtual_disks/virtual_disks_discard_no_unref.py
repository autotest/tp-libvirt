#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import os

from avocado.utils import process

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.virtual_disk import disk_base


def check_result(vm, params, test):
    """
    Check the dumpxml/qemu command line and read/write data in guest
    1) Check the guest dumpxml with expected driver attribute.
    2) Check the qemu command line after starting the guest.
    3) Read/write the data in guest.

    :param vm: vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    check_qemu_pattern = params.get("check_qemu_pattern", "")
    expect_xml_line = params.get("expect_xml_line", "")
    check_libvirtd_log = params.get("check_libvirtd_log", "")
    target_dev = params.get("target_dev")
    hotplug = "yes" == params.get("hotplug", "no")
    test.log.info("Check the dumpxml.")
    libvirt.check_dumpxml(vm, expect_xml_line)
    if check_qemu_pattern:
        test.log.info("Check the qemu command line.")
        libvirt.check_qemu_cmd_line(check_qemu_pattern)
    if hotplug:
        test.log.info("Check the libvirtd log.")
        libvirtd_log_file = os.path.join(test.debugdir, "libvirtd.log")
        libvirt.check_logfile(check_libvirtd_log, libvirtd_log_file)
    test.log.info("Check read/write in guest.")
    libvirt_disk.check_virtual_disk_io(vm, target_dev)


def run_test_start_vm(vm, params, test):
    """
    Scenario: start guest with discard_no_unref attribute

    :param vm: vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    vm_name = params.get("main_vm")
    disk_dict = eval(params.get("disk_dict", "{}"))
    disk_type = params.get("disk_type")
    disk_obj = disk_base.DiskBase(test, vm, params)

    test.log.info("STEP1: prepare the guest xml.")
    new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict)
    if disk_type == "block":
        cmd = "qemu-img create -f qcow2 %s 50M" % new_image_path
        process.run(cmd, shell=True, ignore_status=False)
    test.log.info("STEP2: start the guest.")
    virsh.start(vm_name, debug=True, ignore_status=False)
    test.log.debug("The current guest xml is: %s" % virsh.dumpxml(vm_name).stdout_text)
    test.log.info("STEP3: check the dumpxml and the qemu command line and read/write in guest.")
    check_result(vm, params, test)


def run_test_define_invalid(vm, params, test):
    """
    Scenario: start guest with invalid discard_no_unref configuration

    :param vm: vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    status_error = "yes" == params.get("status_error", "no")
    invalid_format = "yes" == params.get("invalid_format", "no")
    expect_error = params.get("expect_error")
    disk_dict = eval(params.get("disk_dict", "{}"))
    disk_type = params.get("disk_type")
    disk_obj = disk_base.DiskBase(test, vm, params)
    if status_error:
        if invalid_format:
            disk_dict['driver']['type'] = 'raw'
        else:
            disk_dict.update({'readonly': True})
    try:
        disk_obj.add_vm_disk(disk_type, disk_dict)
    except xcepts.LibvirtXMLError as xml_error:
        if not status_error:
            test.fail("Failed to define VM:\n%s" % str(xml_error))
        else:
            test.log.debug("Get expecct error message:\n%s" % expect_error)


def run_test_hotplug_disk(vm, params, test):
    """
    Scenario: hotplug/unplug disk with discard_no_unref enabled

    :param vm: vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    vm_name = params.get("main_vm")
    disk_type = params.get("disk_type")
    target_dev = params.get("target_dev")
    disk_dict = eval(params.get("disk_dict", "{}"))
    disk_obj = disk_base.DiskBase(test, vm, params)

    test.log.debug("STEP1&2: prepare hotplugged disk image and xml.")
    disk_xml, _ = disk_obj.prepare_disk_obj(disk_type, disk_dict)
    if not vm.is_alive():
        vm.start()
    test.log.debug("STEP3: attach disk xml.")
    virsh.attach_device(vm_name, disk_xml.xml, debug=True, ignore_status=False)
    test.log.debug("STEP4: check the result.")
    check_result(vm, params, test)
    test.log.debug("STEP5: detach disk.")
    virsh.detach_device(vm_name, disk_xml.xml, debug=True, ignore_status=False)
    domblklist_result = virsh.domblklist(vm_name, debug=True).stdout_text.strip()
    if target_dev in domblklist_result:
        test.fail("The target disk % can't be detached in guest." % target_dev)


def run_test_update_negative(vm, params, test):
    """
    Scenario: update device for guest with discard_no_unref attribute

    :param vm: vm instance
    :param params: dict, test parameters
    :param test: test object
    """
    vm_name = params.get("main_vm")
    expect_error = params.get("expect_error")

    test.log.debug("STEP1: start a guest.")
    if not vm.is_alive():
        vm.start()
    test.log.debug("STEP2: prepare the disk xml.")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    disk = vmxml.get_devices(device_type="disk")[0]
    disk_driver = disk['driver']
    disk_driver.update({'discard_no_unref': 'on'})
    disk['driver'] = disk_driver
    test.log.debug("STEP3: update the disk device.")
    result = virsh.update_device(vm_name, disk.xml, debug=True)
    libvirt.check_result(result, expect_error)


def teardown_test(vm, vmxml, params, test):
    """
    :param vm: vm instance
    :params vmxml: the guest xml
    :param params: dict, test parameters
    :param test: test object
    """
    disk_type = params.get("disk_type")
    if vm.is_alive():
        vm.destroy()
    vmxml.sync()
    disk_obj = disk_base.DiskBase(test, vm, params)
    disk_obj.cleanup_disk_preparation(disk_type)


def run(test, params, env):
    """
    Test driver attribute: discard_no_unref

    Scenarios:
    1) Start guest with disk discard_no_unref attribute.
    2) Define guest with invalid disk discard_no_unref attribute.
    3) Hotplug disk with discard_no_unref attribute.
    """
    vm_name = params.get("main_vm")
    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    test_scenario = params.get("test_scenario")
    case_name = "run_test_%s" % test_scenario
    run_test_case = eval(case_name)
    try:
        if vm.is_alive():
            vm.destroy()
        run_test_case(vm, params, test)
    finally:
        teardown_test(vm, bkxml, params, test)
