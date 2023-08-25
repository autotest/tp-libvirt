#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Chunfu Wen<chwen@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import logging
import os

from avocado.utils import process

from virttest import libvirt_version
from virttest import virt_vm
from virttest import virsh

from virttest.libvirt_xml import vm_xml, xcepts

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def create_one_disk(params):
    """
    create one disk xml

    :param params: dict wrapped with params
    """
    # Prepare disk source xml
    source_file_path = params.get("source_file_path")
    libvirt.create_local_disk("file", source_file_path, 1,
                              disk_format="qcow2")

    cleanup_files.append(source_file_path)

    disk_src_dict = {"attrs": {"file": source_file_path}}
    target_device = params.get("target_device")

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        "file", "disk",
        target_device, 'scsi',
        'qcow2', disk_src_dict, None)

    driver_dict = params.get("driver_attribute")
    customized_disk.driver = eval(driver_dict)

    LOG.info("disk xml is: %s", customized_disk)
    return customized_disk


def pre_check_host_condition(test):
    """
    Check host whether it meets basic configuration

    :param test: one test object instance
    """
    # Enable io_uring
    enable_cmd_sysctl = "sysctl kernel.io_uring_disabled=0"
    process.run(enable_cmd_sysctl, shell=True)
    # check whether system control output contains kernel.io_uring_disabled = 0, otherwise skip test
    cmd_sysctl = "sysctl kernel.io_uring_disabled"
    sysctl_output = process.run(cmd_sysctl, shell=True).stdout_text.strip()
    if "kernel.io_uring_disabled = 0" not in sysctl_output:
        test.cancel("please enable io_uring by sysctl kernel.io_uring_disabled=0 considering previous output: %s" % sysctl_output)


def test_coldplug_io_uring_normal_start(test, params, env):
    """
    Test coldplug scsi device with io_uring in VM

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    device_obj = create_one_disk(params)
    params.update({'device_obj': device_obj})
    virsh.attach_device(vm_name, device_obj.xml, flagstr="--config",
                        ignore_status=False)
    vm.start()
    vm.wait_for_login().close()


def test_hotplug_io_uring_normal_start(test, params, env):
    """
    Test hotplug scsi device with io_uring in VM

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm.start()
    vm.wait_for_login().close()
    device_obj = create_one_disk(params)
    params.update({'device_obj': device_obj})
    virsh.attach_device(vm_name, device_obj.xml, flagstr="--live",
                        ignore_status=False)


def check_io_uring_device(test, params, env):
    """
    Check io_uring device in VM xml

    :param test: one test object instance
    :param params: dict wrapped with params
    :param env: environment instance
    """
    vm_name = params.get("main_vm")

    io_uring_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    disk_list = io_uring_xml.devices.by_device_tag("disk")

    # Get the disks driver attribute's value.
    for disk in disk_list:
        if disk.target['dev'] != params.get('target_device'):
            continue
        driver_dict = disk.driver
        LOG.debug("driver value:%s", driver_dict)
        if driver_dict.get("io") is None:
            test.fail("failed to get io attribute from: %s" % driver_dict)
        elif driver_dict.get("io") != "io_uring":
            test.fail("get wrong io attribute from : %s" % driver_dict)

    virsh.detach_device(vm_name, params.get('device_obj').xml, flagstr="--live",
                        ignore_status=False)


def run(test, params, env):
    """
    Test disk io tuning related scenarios

    1.Prepare test environment
    2.Prepare disk image with io tuning attribute
    3.Start the domain.
    4.Perform test operation and check result
    5.Recover test environment.
    """
    # Skip test if version not match expected one
    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    # Destroy VM first.
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    coldplug = "yes" == params.get("coldplug")
    define_error = "yes" == params.get("define_error", "no")

    plug_mode = params.get("plug_mode")
    tuning_type = params.get("tuning_type")
    test_scenario = params.get("test_scenario")
    run_test_case = eval("test_%s_%s_%s" % (plug_mode, tuning_type, test_scenario))

    try:
        pre_check_host_condition(test)
        run_test_case(test, params, env)
    except virt_vm.VMStartError as e:
        test.fail("VM failed to start."
                  "Error: %s" % str(e))
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % str(xml_error))
        else:
            if params.get('error_msg') not in str(xml_error):
                test.fail("Get unexpected error message from: %s" % str(xml_error))
    else:
        if test_scenario == "normal_start":
            check_io_uring_device(test, params, env)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        LOG.info("Restoring vm...")
        vmxml_backup.sync()
        # Clean up images
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
