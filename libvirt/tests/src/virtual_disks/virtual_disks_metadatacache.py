import logging
import os
import re

from virttest import libvirt_xml
from virttest import libvirt_version
from virttest import virsh
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk


def test_blockcopy_operation(vm_name, disk_path, disk_format,
                             disk_device, device_target, device_bus, max_blockcopy_size, blockcopy_option, test):
    """
    Test virsh blockcopy operation on disk with metadatacache attribute.

    :param vm_name: domain name
    :param disk_path: the path of disk
    :param disk_format: the format to disk image
    :param disk_device: the disk device type
    :param device_target: the target of disk
    :param max_blockcopy_size: max blockcopy metadatacache size
    :param blockcopy_option: blockcopy option
    :param test: test case itself
    """
    blockcopy_disk = libvirt_disk.create_custom_metadata_disk(disk_path, disk_format, disk_device,
                                                              device_target, device_bus, max_blockcopy_size)
    virsh.blockcopy(vm_name, device_target, "--xml %s" % blockcopy_disk.xml,
                    options=blockcopy_option,
                    debug=True, ignore_status=False)
    #Check job finished
    if not utils_misc.wait_for(lambda: libvirt.check_blockjob(vm_name, device_target, "progress", "100"), 300):
        test.fail("Blockjob timeout in 300 sec.")
    # Check max size value in mirror part
    blk_mirror = ("mirror type='file' file='%s' "
                  "format='%s' job='copy'" % (disk_path, disk_format))
    dom_xml = virsh.dumpxml(vm_name, debug=False).stdout_text.strip()
    if not dom_xml.count(blk_mirror):
        test.fail("Can't see block job in domain xml")
    virsh.blockjob(vm_name, device_target, " --pivot",
                   ignore_status=True, debug=False)
    pivot_xml = virsh.dumpxml(vm_name, debug=True).stdout_text.strip()
    pivot_byte_str = "<max_size unit='bytes'>1000</max_size>"
    if pivot_byte_str not in pivot_xml:
        test.fail("Failed to generate metadata_cache in %s" % pivot_xml)


def test_snapshot_opearation(vm_name, disk_path, disk_format,
                             max_blockcopy_size, test):
    """
    Test snapshot operation on disk with metadata_cache

    :param vm_name: domain name
    :param disk_path: the path of disk
    :param disk_format: the format to disk image
    :param max_blockcopy_size: max metadata_cache size
    :param test: test case itself
    """
    snap_xml = libvirt_xml.SnapshotXML()
    snapshot_name = "snapshot_test"
    snap_xml.snap_name = snapshot_name
    snap_xml.description = "Snapshot Test"
    # Add all disks into xml file.
    vmxml = libvirt_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    disks = vmxml.devices.by_device_tag('disk')
    # Remove non-storage disk such as 'cdrom'
    for disk in disks:
        if disk.device != 'disk':
            disks.remove(disk)
    new_disks = []
    for src_disk_xml in disks:
        disk_xml = snap_xml.SnapDiskXML()
        disk_xml.disk_name = src_disk_xml.target['dev']
        if 'vda' in disk_xml.disk_name:
            disk_xml.snapshot = "no"
        elif 'vdb' in disk_xml.disk_name:
            libvirt_disk.create_custom_metadata_disk(disk_path, disk_format, None,
                                                     None, None, max_blockcopy_size, disk_xml)
        new_disks.append(disk_xml)
    snap_xml.set_disks(new_disks)
    snapshot_xml_path = snap_xml.xml
    logging.debug("The snapshot xml is: %s" % snap_xml.xmltreefile)
    options = " %s --disk-only --no-metadata" % snapshot_xml_path
    virsh.snapshot_create(vm_name, options, debug=True, ignore_status=False)
    snapshot_xml = virsh.dumpxml(vm_name, debug=True).stdout_text.strip()
    logging.debug(snapshot_xml)
    snapshot_part_xml = re.findall(r'(<metadata_cache)(.+)((?:\n.+)+)(metadata_cache>)', snapshot_xml)
    snapshot_filter_str = ''.join(snapshot_part_xml[0])
    snapshot_byte_match = "<max_size unit='bytes'>100</max_size>"
    if snapshot_byte_match not in snapshot_filter_str:
        test.fail("Failed to generate metadata_cache in snapshot operation %s" % snapshot_filter_str)


def test_blockcommit_operation(vm_name, device_target, blockcommit_option, test):
    """
    Test blockcommit on disk with metadata_cache size attribute

    :param vm_name: domain name
    :param disk_target: the target of disk
    :param blockcommit_option: blockcommit option
    :param test: test case itself
    """
    virsh.blockcommit(vm_name, device_target,
                      blockcommit_option, ignore_status=False, debug=True)
    # Check max size value in mirror part
    mirror_xml = virsh.dumpxml(vm_name, debug=False).stdout_text.strip()
    mirror_part_xml = re.findall(r'(<mirror)(.+)((?:\n.+)+)(mirror>)', mirror_xml)
    mirror_filter_str = ''.join(mirror_part_xml[0])
    mirror_byte_match = "<max_size unit='bytes'>1000</max_size>"
    if mirror_byte_match not in mirror_filter_str:
        test.fail("Failed to generate metadata_cache in blockcommit operation %s" % mirror_filter_str)
    virsh.blockjob(vm_name, device_target, " --pivot",
                   ignore_status=True, debug=True)
    pivot_xml = virsh.dumpxml(vm_name, debug=False).stdout_text.strip()
    if mirror_byte_match not in pivot_xml:
        test.fail("Failed to generate metadata_cache in  blockcommit %s" % pivot_xml)


def test_batch_cdrom_operation(vm_name, disk_format, disk_device, device_target, device_bus, test):
    """
    Test cdrom on disk with different metadata_cache size attribute

    :param vm_name: domain name
    :param disk_format: disk format
    :param disk_device: device name
    :param disk_target: the target of disk
    :param device_bus: device bus
    :param test: test case itself
    """

    for size_type in ["100", "1000", "10"]:
        disk_path = "/var/lib/libvirt/images/test_image_%s" % size_type
        if size_type == "10":
            disk_path = ""
        if disk_path:
            libvirt.create_local_disk("file", disk_path, "100M", disk_format)
        disk_size = libvirt_disk.create_custom_metadata_disk(disk_path, disk_format, disk_device,
                                                             device_target, device_bus, size_type)
        virsh.update_device(vm_name, disk_size.xml, flagstr='--live',
                            ignore_status=False, debug=True)
        # Check max size value in cdrom part
        cdrom_xml = virsh.dumpxml(vm_name, debug=False).stdout_text.strip()
        cdrom_part_xml = re.findall(r'(<metadata_cache)(.+)((?:\n.+)+)(metadata_cache>)', cdrom_xml)
        cdrom_filter_str = ''.join(cdrom_part_xml[0])
        cdrom_byte_match = "<max_size unit='bytes'>%s</max_size>" % size_type
        if cdrom_byte_match not in cdrom_filter_str:
            test.fail("Failed to generate metadata_cache in cdrom operation %s" % cdrom_part_xml)


def run(test, params, env):
    """
    Test blockcopy,snapshot,blockcommit on disk with metadata_cache attribute.

    1.Prepare test environment
    2.Prepare disk image with metadata_cache attribute
    3.Start the domain.
    4.Perform test operation and check metadata_cache size change
    5.Recover test environment.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    # disk related parameters
    disk_format = params.get("disk_format")
    disk_path = params.get("disk_path")
    disk_device = params.get("disk_device", "disk")
    device_target = params.get("device_target", 'vdb')
    disk_format = params.get("disk_format", "qcow2")
    device_bus = params.get("device_bus", "virtio")

    # blockcopy,snapshot,blockcommit related paremeter
    max_cache_size = params.get("max_cache_size", "10")
    max_blockcopy_size = params.get("mirror_max_cache_size", "1000")
    max_snapshot_size = params.get("snapshot_max_cache_size", "100")
    attach_option = params.get("attach_option")
    test_blockcopy_path = "%s.copy" % disk_path
    test_snapshot_path = "%s.s1" % disk_path
    blockcommit_option = params.get("blockcommit_option", "--active --wait --verbose")
    blockcopy_option = params.get("blockcopy_option", "--transient-job --wait --verbose")
    test_batch_block_operation = "yes" == params.get("test_batch_block_operation", "no")
    test_batch_cdrom = "yes" == params.get("test_batch_cdrom_operation", "no")

    # Destroy VM first.
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vmxml_backup.copy()

    try:
        if not libvirt_version.version_compare(7, 0, 0):
            test.cancel("current libvirt version doesn't support metadata_cache feature")
        # Build new disk.
        libvirt.create_local_disk('file', disk_path, '10', disk_format)
        custom_disk = libvirt_disk.create_custom_metadata_disk(disk_path, disk_format,
                                                               disk_device, device_target, device_bus, max_cache_size)
        hot_plug = "yes" == params.get("hot_plug", "no")

        # If hot plug, start VM first, otherwise stop VM if running.
        if hot_plug:
            vm.start()
            virsh.attach_device(domainarg=vm_name, filearg=custom_disk.xml,
                                flagstr=attach_option,
                                dargs=virsh_dargs, debug=True, ignore_status=False)
        else:
            vmxml.add_device(custom_disk)
            vmxml.sync()
            vm.start()
        vm.wait_for_login().close()

        if test_batch_block_operation:
            test_blockcopy_operation(vm_name, test_blockcopy_path, disk_format,
                                     disk_device, device_target,
                                     device_bus, max_blockcopy_size, blockcopy_option, test)
            test_snapshot_opearation(vm_name, test_snapshot_path, disk_format,
                                     max_snapshot_size, test)
            test_blockcommit_operation(vm_name, device_target, blockcommit_option, test)
        if test_batch_cdrom:
            test_batch_cdrom_operation(vm_name, disk_format, disk_device, device_target, device_bus, test)
        # Detach hot plugged device.
        if hot_plug:
            virsh.detach_device(vm_name, custom_disk.xml,
                                flagstr=attach_option, dargs=virsh_dargs, ignore_status=False, wait_remove_event=True)
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        for file_path in [disk_path, test_blockcopy_path, test_snapshot_path]:
            if os.path.exists(file_path):
                os.remove(file_path)
        logging.info("Restoring vm...")
        vmxml_backup.sync()
