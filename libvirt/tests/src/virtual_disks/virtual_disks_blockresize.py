#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Chunfu Wen <chwen@redhat.com>
#

import logging
import os
import time

from virttest import libvirt_version
from virttest import virsh
from virttest import virt_vm
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from avocado.utils import process


LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def setup_scsi_debug_block_device():
    """
    Setup one scsi_debug block device

    :return: device name
    """
    source_disk = libvirt.create_scsi_disk(scsi_option="",
                                           scsi_size="100")
    return source_disk


def check_image_virtual_size(image_file, expected_size, test):
    """
    Check whether image virtual size queried by qemu-img is matched

    :param image_file: image file path
    :param expected_size: expected size
    :param test: test case itself
    """
    disk_info_dict = utils_misc.get_image_info(image_file)
    actual_size_in_bytes = str(disk_info_dict.get("vsize"))
    if actual_size_in_bytes != expected_size:
        test.fail(f"actual size of block disk is {actual_size_in_bytes}, but expected is : {expected_size}")


def create_customized_disk(params, test):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :param test: test case itself
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file_path = None
    source_raw_file_path = params.get("source_raw_file_path")
    if source_raw_file_path:
        libvirt.create_local_disk("file", source_raw_file_path, "1M", device_format)
        cleanup_files.append(source_raw_file_path)
        source_file_path = source_raw_file_path
    else:
        source_file_path = setup_scsi_debug_block_device()
    overlay_source_file_path = params.get("overlay_source_file_path")
    expected_size = params.get("block_size_in_bytes")
    source_dict = {}

    # check block size
    check_image_virtual_size(source_file_path, expected_size, test)

    if source_raw_file_path:
        source_dict.update({"file": source_file_path})
    else:
        source_dict.update({"dev": source_file_path})

    if overlay_source_file_path:
        libvirt.create_local_disk("file", overlay_source_file_path, "100M", device_format)
        cleanup_files.append(overlay_source_file_path)
        convert_cmd = "qemu-img convert -f qcow2 -O qcow2 %s %s" % (overlay_source_file_path, source_file_path)
        process.run(convert_cmd, shell=True, verbose=True, ignore_status=False)

    disk_src_dict = {"attrs": source_dict}

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)

    # Sometimes, slice size can be gotten by command (du -b source_file_path), but here not necessary
    disk_slice_attrs = params.get('disk_slice_attrs')
    if disk_slice_attrs:
        disk_source = customized_disk.source
        disk_source.slices = customized_disk.new_slices(**eval(disk_slice_attrs))
        customized_disk.source = disk_source

    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def check_slice_within_vm(vm, new_disk, slice_value):
    """
    Check disk slices attributes in guest internal

    :param vm: vm object
    :param new_disk: newly vm disk
    :param slice_value: slice value
    """
    session = None
    try:
        session = vm.wait_for_login()
        cmd = ("lsblk |grep {0}"
               .format(new_disk))
        status, output = session.cmd_status_output(cmd)
        LOG.debug("Disk operation in VM:\nexit code:\n%s\noutput:\n%s",
                  status, output)
        return slice_value in output
    except Exception as err:
        LOG.debug("Error happens when check slices in vm: %s", str(err))
        return False
    finally:
        if session:
            session.close()


def check_vm_dumpxml(params, test, expected_attribute=True):
    """
    Common method to check source in cdrom device

    :param params: one collective object representing wrapped parameters
    :param test: test object
    :param expected_attribute: bool indicating whether expected attribute exists or not
    """
    vm_name = params.get("main_vm")
    disk_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    target_dev = params.get('target_dev')
    disk = disk_vmxml.get_disk_all()[target_dev]
    slices = disk.find('source').find('slices')
    if not expected_attribute:
        if slices is not None:
            test.fail("unexpected slices appear in vm disk xml")
    else:
        if slices is None:
            test.fail("slices can not be found in vm disk xml")


def run(test, params, env):
    """
    Test resize disk with slices.

    1.Prepare a vm with block disk
    2.Attach block disk with slice attribute to the vm
    3.Start vm
    4.Resize the block disk
    5.Check resize operations
    6.Check status in VM internal
    7.Detach device
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True}

    hotplug = "yes" == params.get("virt_device_hotplug")
    part_path = "/dev/%s"

    # Skip test if version not match expected one
    libvirt_version.is_libvirt_feature_supported(params)

    # Get disk partitions info before hot/cold plug virtual disk
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Backup vm xml
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vmxml_backup.copy()

    test_scenario = params.get("test_scenario")
    target_bus = params.get("target_bus")
    target_dev = params.get('target_dev')
    status_error = "yes" == params.get("status_error")
    type_name = params.get("type_name")
    source_raw_file_path = params.get("source_raw_file_path")
    try:
        device_obj = create_customized_disk(params, test)
        if not hotplug:
            vmxml.add_device(device_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login()
        if hotplug:
            virsh.attach_device(vm_name, device_obj.xml,
                                ignore_status=False, debug=True)
    except xcepts.LibvirtXMLError as xml_error:
        test.fail("Failed to define VM:\n%s" % str(xml_error))
    except virt_vm.VMStartError as details:
        test.fail("VM failed to start."
                  "Error: %s" % str(details))
    else:
        session = vm.wait_for_login()
        time.sleep(20)
        new_disk, _ = libvirt_disk.get_non_root_disk_name(session)
        session.close()
        check_vm_dumpxml(params, test, True)

        slice_value = params.get("offset")
        check_slice_within_vm(vm, new_disk, slice_value)

        if test_scenario in ["blockresize_save_restore"]:
            vm_save = params.get("vm_save")
            cleanup_files.append(vm_save)
            virsh.save(vm_name, vm_save)
            virsh.restore(vm_save)
            vm.wait_for_login().close()

        resize_value = params.get("resize_value")
        result = virsh.blockresize(vm_name, target_dev,
                                   resize_value, **virsh_dargs)
        libvirt.check_exit_status(result, status_error)
        if test_scenario in ["not_align_with_multiple_1024"]:
            actual_expectd_size = params.get("actual_resize_value")
            check_image_virtual_size(source_raw_file_path, actual_expectd_size, test)

        if not status_error:
            check_vm_dumpxml(params, test, False)
            slice_value = params.get("size_in_mb")
            check_slice_within_vm(vm, new_disk, slice_value)
        if hotplug:
            virsh.detach_device(vm_name, device_obj.xml, flagstr="--live",
                                debug=True, ignore_status=False)
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        if type_name in ["block"]:
            libvirt.delete_scsi_disk()
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
