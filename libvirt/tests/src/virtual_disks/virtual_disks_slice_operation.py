import logging
import os

from avocado.utils import process

from virttest import libvirt_xml
from virttest import virt_vm
from virttest import virsh

from virttest.libvirt_xml import vm_xml, xcepts
from virttest.utils_libvirt import libvirt_disk

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def create_customized_disk(params):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = params.get("target_dev")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file_path = params.get("virt_disk_device_source")
    source_dict = {}

    if source_file_path:
        cmd = "qemu-img create -f %s %s %s %s" % (device_format,
                                                  "-o preallocation=full", source_file_path, "100M")
        process.run(cmd, shell=True, ignore_status=True)
        cleanup_files.append(source_file_path)
        if 'block' in type_name:
            source_dict.update({"dev": source_file_path})
        else:
            source_dict.update({"file": source_file_path})

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

    additional_driver_attrs = params.get("additional_driver_attrs")
    if additional_driver_attrs:
        customized_disk.driver = dict(customized_disk.driver, **eval(additional_driver_attrs))

    if disk_device == "cdrom":
        customized_disk.readonly = True
    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def create_snapshot_xml(params, vm_name, index):
    """
    Create one snapshot disk with related attributes

    :param params: dict wrapped with params
    :param vm_name: VM instance name
    :param index: snapshot file index
    :return return one tuple with snap_xml and snap files as elements
    """
    snap_xml = libvirt_xml.SnapshotXML()
    snap_xml.snap_name = "snapshot_create_%s" % index
    snap_xml.description = "Snapshot Test"
    # Add all disks into xml file.
    vmxml = libvirt_xml.VMXML.new_from_dumpxml(vm_name)
    disks = vmxml.devices.by_device_tag('disk')
    # Remove non-storage disk such as 'cdrom'
    for disk in disks:
        if disk.device != 'disk':
            disks.remove(disk)
    new_disks = []
    for src_disk_xml in disks:
        disk_xml = snap_xml.SnapDiskXML()
        disk_target = src_disk_xml.target['dev']
        disk_xml.disk_name = disk_target
        if 'vda' in disk_xml.disk_name:
            disk_xml.snapshot = "no"
        elif 'vdb' in disk_xml.disk_name:
            disk_xml.snapshot = "external"
            driver_type = "qcow2"
            snapshot_file = "%s_snapshot_%s_%s" % (
                src_disk_xml.xmltreefile.find('source').get('file'), disk_target, index)
            disk_slice_attrs = params.get('disk_slice__override_attrs')
            update_snapshot_disk(disk_xml, snapshot_file, eval(disk_slice_attrs), driver_type)
        new_disks.append(disk_xml)
    snap_xml.set_disks(new_disks)
    LOG.debug("create_snapshot xml: %s", snap_xml)
    return snap_xml, snapshot_file


def update_snapshot_disk(disk_xml, snapshot_file=None, disk_slice_attrs=None, driver_type="qcow2"):
    """
    Update snapshot disk xml with related attributes

    :param disk_xml: disk xml will be updated
    :param snapshot_file: snapshot file attribute in the disk
    :param disk_slice_attrs: slice attribute in the disk
    :param driver_type: driver type
    :return: updated snapshot disk
    """
    source_dict = {}
    driver_dict = {'type': driver_type}
    if snapshot_file:
        cmd = "qemu-img create -f %s %s %s %s" % (driver_type,
                                                  "-o preallocation=full", snapshot_file, "100M")
        process.run(cmd, shell=True, ignore_status=True)
        source_dict.update({"file": snapshot_file})
        cleanup_files.append(snapshot_file)

    disk_xml.source = disk_xml.new_disk_source(**{"attrs": source_dict})
    disk_xml.driver = driver_dict
    if disk_slice_attrs:
        disk_source = disk_xml.source
        disk_source.slices = disk_xml.new_slices(**disk_slice_attrs)
        disk_xml.source = disk_source

    LOG.debug("update customized xml: %s", disk_xml)
    return disk_xml


def create_empty_cdrom_xml(params):
    """
    Create one empty cdrom xml

    :param params: wrapped parameters in dictionary format
    :return: empty cdrom xml
    """
    if 'virt_disk_device_source' in params:
        params.pop('virt_disk_device_source')
    return create_customized_disk(params)


def translate_raw_output_into_dict(output, default_delimiter="="):
    """
    A utility to translate raw output with key=value attribute into one dictionary

    :param output: raw output
    :param default_delimiter: delimiter to separate line
    :return one dictionary
    """
    data_dict = {}
    for line in output.split('\n'):
        if default_delimiter in line:
            data_dict.update({"%s" % line.split(default_delimiter)[0].strip():
                             "%s" % line.split(default_delimiter)[1].strip()})
    return data_dict


def check_slice_hot_operate(vm, params, test):
    """
    Check hot operation on disk with slice attribute

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    device_target = params.get("target_dev")
    try:
        session = vm.wait_for_login()
        _, output = session.cmd_status_output(
            "dd if=/dev/zero of=/dev/%s bs=10M count=1" % device_target)
        LOG.info("Fill contents in VM:\n%s", output)
        session.close()
    except Exception as e:
        LOG.error(str(e))
        session.close()

    domstats_raw_output = virsh.domstats(vm.name, "--block",
                                         ignore_status=False, debug=True).stdout_text.strip()
    domstats_dict = translate_raw_output_into_dict(domstats_raw_output)
    domblkinfo_raw_output = virsh.domblkinfo(vm.name, device_target,
                                             ignore_status=False, debug=True).stdout_text.strip()
    domblkinfo_dict = translate_raw_output_into_dict(domblkinfo_raw_output, default_delimiter=":")

    # Verify allocation, capacity, and physical are the same from domstats and domblkinfo
    for key in ['Allocation', 'Capacity', 'Physical']:
        if domstats_dict["block.1.%s" % key.lower()] != domblkinfo_dict[key]:
            test.fail("The domstats value: %s is not equal to domblkinfo: %s"
                      % (domstats_dict["block.1.%s" % key.lower()], domblkinfo_dict[key]))


def check_slice_cdrom_update(vm, test):
    """
    Check cdrom with slice attribute

    :param vm: one object representing VM
    :param test: test assert object
    """
    try:
        session = vm.wait_for_login()
        mnt_cmd = "mount /dev/sr0 /mnt && ls /mnt"
        status = session.cmd_status(mnt_cmd)
        if status:
            test.fail("Failed to mount cdrom device in VM")
    except Exception as e:
        LOG.error(str(e))
    finally:
        session.close()


def check_slice_disk_blockcommit(vm, params):
    """
    Check blockcommit on disk with slice attribute

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    """
    snapshot_counts = int(params.get("snapshot_counts"))
    snapshot_files = []
    for index in range(snapshot_counts):
        snapshot_xml, snapshot_file = create_snapshot_xml(params, vm.name, index)
        snapshot_option = " %s --no-metadata --disk-only --reuse-external" % snapshot_xml.xml
        virsh.snapshot_create(vm.name, snapshot_option, debug=True, ignore_status=False)
        snapshot_files.append(snapshot_file)
    device_target = params.get("target_dev")

    debug_vm = vm_xml.VMXML.new_from_dumpxml(vm.name)
    phase_one_blockcommit_options = " --top %s --shallow --wait --verbose" % snapshot_files[0]
    libvirt_disk.do_blockcommit_repeatedly(vm, device_target, phase_one_blockcommit_options, 1)

    vm.wait_for_login().close()
    phase_two_blockcommit_options = " --active --wait --verbose --pivot"
    libvirt_disk.do_blockcommit_repeatedly(vm, device_target, phase_two_blockcommit_options, 1)


def check_slice_disk_blockcopy(vm, params):
    """
    Check blockcopy on disk with slice attribute

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    """

    device_target = params.get("target_dev")
    params.update({"virt_disk_device_source": params.get("virt_disk_blockcopy")})
    blockcopy_disk = create_customized_disk(params)

    debug_vm = vm_xml.VMXML.new_from_dumpxml(vm.name)
    blockcopy_options = " --wait --verbose --pivot --reuse-external --transient-job"
    virsh.blockcopy(vm.name, device_target, " --xml %s" % blockcopy_disk.xml,
                    options=blockcopy_options, ignore_status=False,
                    debug=True)


def check_slice_blkdeviotune(vm, params, test):
    """
    Check block blkdeviotune on disk with slice attribute

    :param vm: one object representing VM
    :param params: wrapped parameters in dictionary format
    :param test: test assert object
    """
    device_target = params.get("target_dev")
    total_bytes_sec = params.get("total_bytes_sec")

    virsh.blkdeviotune(vm.name, device_target, " --total-bytes-sec %s" % total_bytes_sec,
                       ignore_status=False, debug=True)
    blkdeviotune_raw_output = virsh.blkdeviotune(vm.name, device_target,
                                                 ignore_status=False, debug=True).stdout_text.strip()
    blkdeviotune_dict = translate_raw_output_into_dict(blkdeviotune_raw_output, default_delimiter=":")

    LOG.debug(blkdeviotune_dict)
    # Verify total_bytes_sec is the same with preset one
    if blkdeviotune_dict["total_bytes_sec"] != total_bytes_sec:
        test.fail("The gotten value: %s is not equal from preset one" % blkdeviotune_dict["total_bytes_sec"])


def run(test, params, env):
    """
    Test attach cdrom device with option.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test xml for cdrom devices.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    # Disk specific attributes.
    image_path = params.get("virt_disk_device_source", "/var/lib/libvirt/images/test.img")

    scenario_execution = params.get("scenario_execution")

    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error", "no")

    device_obj = None
    # Back up xml file.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Create disk xml with slice attribute
        device_obj = create_customized_disk(params)
        vmxml.add_device(device_obj)
        vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
    except virt_vm.VMStartError as e:
        if status_error:
            LOG.debug("VM failed to start as expected."
                      "Error: %s", str(e))
        else:
            test.fail("VM failed to start."
                      "Error: %s" % str(e))
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % xml_error)
        else:
            LOG.info("As expected, failed to define VM")
    except Exception as ex:
        test.fail("unexpected exception happen: %s" % str(ex))
    else:
        if scenario_execution == "slice_hot_operate":
            # Need detach slice disk, then attach it again
            virsh.detach_device(vm_name, device_obj.xml, flagstr="--live", ignore_status=False, debug=True)
            vm.wait_for_login().close()
            virsh.attach_device(vm_name, device_obj.xml, flagstr="--live", ignore_status=False, debug=True)
            check_slice_hot_operate(vm, params, test)
        elif scenario_execution == "slice_cdrom_update":
            # Empty cdrom device
            empty_cdrom = create_empty_cdrom_xml(params)
            virsh.update_device(vm_name, empty_cdrom.xml, flagstr="--live", ignore_status=False, debug=True)
            vm.wait_for_login().close()
            virsh.update_device(vm_name, device_obj.xml, flagstr="--live", ignore_status=False, debug=True)
            check_slice_cdrom_update(vm, test)
        elif scenario_execution == "slice_disk_blockcommit":
            check_slice_disk_blockcommit(vm, params)
        elif scenario_execution == "slice_disk_blockcopy":
            check_slice_disk_blockcopy(vm, params)
        elif scenario_execution == "slice_blkdeviotune":
            check_slice_blkdeviotune(vm, params, test)
    finally:
        libvirt_disk.cleanup_snapshots(vm)
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        LOG.info("Restoring vm...")
        vmxml_backup.sync()
        # Clean up images
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
