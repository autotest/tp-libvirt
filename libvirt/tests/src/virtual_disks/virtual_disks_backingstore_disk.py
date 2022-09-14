import logging
import os

from avocado.utils import process

from virttest import virt_vm
from virttest import virsh

from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml import vm_xml

from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)

cleanup_files = []


def create_customized_disk(params):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :return: return disk device
    """
    type_name = params.get("type_name")
    device_target = params.get("target_dev")
    disk_device = params.get("device_type")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    virt_disk_device_source_overlay = params.get("virt_disk_device_source_overlay")
    overlay_volume_name = params.get("overlay_image_name")
    pool_name = params.get("pool_name")
    source_dict = {'pool': pool_name, 'volume': overlay_volume_name}
    if virt_disk_device_source_overlay:
        base_image_file = params.get("virt_disk_device_source_base")
        libvirt.create_local_disk("file", base_image_file, 1, device_format)
        cleanup_files.append(base_image_file)
        create_overlay_cmd = "qemu-img create -f %s %s -F %s -b %s" % (
                              device_format, virt_disk_device_source_overlay, device_format, base_image_file)
        process.run(create_overlay_cmd, ignore_status=True, shell=True)
        cleanup_files.append(virt_disk_device_source_overlay)
        virsh.pool_refresh(pool_name)

    disk_src_dict = {"attrs": source_dict}

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)

    # Add backing store element if needed
    backingstore_type = params.get("backingstore_type")
    if backingstore_type:
        backingstore_volume_name = params.get("image_base_name")
        backingstore = Disk.BackingStore()
        backingstore.type = backingstore_type
        backingstore.format = {'type': device_format}
        backingstore_source = {'attrs': {'pool': pool_name, 'volume': backingstore_volume_name}}
        src1 = backingstore.new_source(**backingstore_source)
        backingstore.source = src1
        customized_disk.backingstore = backingstore

    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def run(test, params, env):
    """
    Test start Vm with disk backingStore type = volume - bug 1804603
    And strongly related to https://bugzilla.redhat.com/show_bug.cgi?id=1804603

    1.Prepare test environment with provisioned VM
    2.Prepare test xml.
    3.Perform start VM and check VM status
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    coldplug = "yes" == params.get("virt_device_coldplug")

    # Back up xml file
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        vmxml = vmxml_backup.copy()
        # Create disk with volume type backing file format
        device_obj = create_customized_disk(params)
        if coldplug:
            vmxml.add_device(device_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
    except virt_vm.VMStartError as e:
        test.fail("VM failed to start."
                  "Error: %s" % str(e))
    finally:
        # Recover VM
        LOG.info("Restoring vm...")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        # Clean up files
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
