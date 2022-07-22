import logging
import os

from virttest import libvirt_version
from virttest import libvirt_xml
from virttest import virsh
from virttest import virt_vm

from virttest.libvirt_xml import vm_xml, xcepts

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def create_customized_disk(params, target_dev, snapshot_mode=None):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :param target_dev: device target
    :param snapshot_mode: snapshot mode in disk
    :return: disk object
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_target = target_dev
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file_path = "%s.%s" % (params.get("virt_disk_device_source"), target_dev)
    source_dict = {}

    if source_file_path:
        libvirt.create_local_disk("file", source_file_path, 1,
                                  disk_format=device_format)
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

    if snapshot_mode:
        customized_disk.snapshot = snapshot_mode

    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def dump_snapshot_xml(vm_name, snapshot):
    """
    Return new snapshot attributes from virsh snapshot dumpxml command

    :param vm_name: Name of VM to dumpxml
    :param snapshot: dump snapshot name
    :return: wrapping dict with snapshot attributes
    """
    snap_xml = libvirt_xml.SnapshotXML()
    result = virsh.snapshot_dumpxml(vm_name, snapshot)
    snap_xml['xml'] = result.stdout_text.strip()
    disk_nodes = snap_xml.xmltreefile.find('disks').findall('disk')
    snapshots = {}
    for node in disk_nodes:
        dev = node.get('name')
        snapshot = node.get('snapshot')
        snapshots[dev] = snapshot
    return snapshots


def create_snapshot_xml(params, vm_name):
    """
    Create one snapshot disk with related attributes

    :param params: dict wrapped with params
    :param vm_name: VM instance name
    :return return snap_xml
    """
    snap_xml = libvirt_xml.SnapshotXML()

    snapshot_name = params.get('snapshot_name')
    if snapshot_name:
        snap_xml.snap_name = snapshot_name
    snap_xml.description = "Snapshot Test"
    snapshot_file = params.get('snapshot_file')
    if snapshot_file:
        snap_xml.mem_file = snapshot_file
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
        if 'vda' in disk_target:
            disk_xml.snapshot = "external"
        if 'vdb' in disk_target:
            disk_xml.snapshot = "external"
        elif 'vdc' in disk_target:
            disk_xml.snapshot = "manual"
        new_disks.append(disk_xml)
    snap_xml.set_disks(new_disks)
    LOG.debug("create_snapshot xml: %s", snap_xml)
    return snap_xml


def translate_domblklist_output_into_dict(vm_name):
    """
    A utility to translate raw output with key=value attribute into one dictionary

    :param output: raw output
    :return one dictionary
    """
    data_dict = {}
    blklist = virsh.domblklist(vm_name).stdout_text.splitlines()
    for line in blklist:
        if line.strip().startswith(('hd', 'vd', 'sd', 'xvd')):
            data_dict.update({"%s" % line.split()[0].strip():
                              "%s" % line.split()[1].strip()})
    return data_dict


def check_vm_paused(params, vm, test, snapshot_func=virsh.snapshot_create_as, snapshot_opt=None):
    """
    Check VM paused when creating snapshot with manual mode

    :params params: wrapped dict with all parameters
    :param vm: one VM instance
    :param test: test assert object
    """
    snapshot_name = params.get('snapshot_name')
    snapshot_file = params.get('snapshot_file')
    cleanup_files.append(snapshot_file)

    # Create snapshot, and then VM should be in paused state since manual mode is applied
    if snapshot_opt:
        snapshot_option = snapshot_opt
    else:
        snapshot_option = "%s --memspec file=%s" % (snapshot_name, snapshot_file)
    snapshot_func(vm.name, snapshot_option, ignore_status=False, debug=True)
    if not libvirt.check_vm_state(vm.name, "paused"):
        test.fail("VM should be paused!")
    virsh.snapshot_dumpxml(vm.name, snapshot_name, debug=True)
    # Validate from virsh domblklist command
    dom_blk_dict = translate_domblklist_output_into_dict(vm.name)
    for item, value in dom_blk_dict.items():
        if snapshot_name in value:
            cleanup_files.append(value)
        if item in ['vdc'] and snapshot_name in value:
            test.fail("Wrongly create snapshot on vdc")
    # Validate from snapshot dumpxml
    for key, value in dump_snapshot_xml(vm.name, snapshot_name).items():
        if key in ['vdb'] and value not in ['external']:
            test.fail("snapshot attribute should be 'external' but found '%s' for vdb " % value)
        if key in ['vdc'] and value not in ['manual']:
            test.fail("snapshot attribute should be 'manual' but found '%s' for vdc " % value)
    virsh.resume(vm.name)
    vm.wait_for_login().close()
    virsh.snapshot_delete(vm.name, snapshot_name, "--metadata", ignore_status=False, debug=True)
    if not os.path.exists(snapshot_file):
        test.fail("Failed to create snapshot file: %s" % snapshot_file)


def run(test, params, env):
    """
    Test set snapshot='external' mode as newly added feature

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test xml for different devices.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Disk specific attributes.
    backend_device = params.get("backend_device", "disk")
    target_device_list = params.get("target_dev").split()
    snapshot_mode_list = params.get("snapshot_mode").split()

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error", "no")
    error_msg = params.get("error_msg", "external mode can not be set")

    device_obj = None

    libvirt_version.is_libvirt_feature_supported(params)
    # Back up xml file.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    try:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Setup two additional disks here
        for target_device, snapshot_mode in zip(target_device_list, snapshot_mode_list):
            if backend_device in ["external_manual_mode_memdump",
                                  "external_manual_mode_with_specified_memory_file"]:
                snapshot_mode = None
            device_obj = create_customized_disk(params, target_device, snapshot_mode)
            if not hotplug:
                vmxml.add_device(device_obj)
                vmxml.sync()
            else:
                if not vm.is_alive():
                    vm.start()
                    vm.wait_for_login().close()
                LOG.info("Attaching devices...")
                result = virsh.attach_device(vm_name, device_obj.xml, debug=True)
                libvirt.check_result(result, expected_fails=[])
        # Start VM when cold plug
        if not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()
    except virt_vm.VMStartError as e:
        test.fail("VM failed to start."
                  "Error: %s" % str(e))
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % str(xml_error))
        else:
            LOG.info("As expected, failed to define VM due to reason:\n%s", str(xml_error))
    except Exception as ex:
        test.fail("unexpected exception happen: %s" % str(ex))
    else:
        if backend_device in ["external_manual_mode"]:
            check_vm_paused(params, vm, test)
        elif backend_device in ["external_manual_mode_memdump"]:
            snapshot_name = params.get('snapshot_name')
            snapshot_file = params.get('snapshot_file')

            snapshot_opt = '%s' % snapshot_name
            target_device_list = params.get("target_dev").split()
            target_device_list.insert(0, 'vda')

            snapshot_mode_list = params.get("snapshot_mode").split()
            snapshot_mode_list.insert(0, 'external')

            for target_device, snapshot_mode in zip(target_device_list, snapshot_mode_list):
                snapshot_opt += ' --diskspec %s,snapshot=%s ' % (target_device, snapshot_mode)
            snapshot_opt += ' --memspec file=%s' % snapshot_file
            check_vm_paused(params, vm, test, snapshot_opt=snapshot_opt)
        elif backend_device in ["external_manual_mode_with_specified_memory_file"]:
            snapshot_xml = create_snapshot_xml(params, vm_name)
            snapshot_opt = snapshot_xml.xml
            check_vm_paused(params, vm, test, snapshot_func=virsh.snapshot_create, snapshot_opt=snapshot_opt)
    finally:
        libvirt.clean_up_snapshots(vm_name)
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        LOG.info("Restoring vm...")
        vmxml_backup.sync()
        # Clean up images
        for file_path in cleanup_files:
            if os.path.exists(file_path):
                os.remove(file_path)
