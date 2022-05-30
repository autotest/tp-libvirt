import logging
import os

from avocado.utils import process

from virttest import libvirt_version
from virttest import virt_vm, utils_misc

from virttest.libvirt_xml import vm_xml, xcepts

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def create_customized_disk(params, device_target, source_file_path):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :param device_target: device target
    :param source_file_path: source file path
    """
    type_name = params.get("type_name")
    disk_device = params.get("device_type")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_dict = {}
    if source_file_path:
        if 'block' in type_name:
            source_dict.update({"dev": source_file_path})
        else:
            source_dict.update({"file": source_file_path})
    disk_src_dict = {"attrs": source_dict}

    addr_str = params.get("addr_attrs")

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    if addr_str:
        addr_dict = eval(addr_str)
        customized_disk.address = customized_disk.new_disk_address(
            **{"attrs": addr_dict})
    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def create_multiple_file_source_disks(params):
    """
    Create multiple file source disks

    :param params: dict wrapped with params
    """
    device_format = params.get("target_format")
    source_file_path = params.get("virt_disk_device_source")
    source_file_list = ["%s.1" % source_file_path, "%s.2" % source_file_path, "%s.3" % source_file_path]
    device_target_list = ['vdb', 'vdc', 'vdd']
    created_file_source_disks = []

    for device_target, source_file in zip(device_target_list, source_file_list):
        libvirt.create_local_disk("file", source_file, 1, device_format)
        cleanup_files.append(source_file)
        source_disk = create_customized_disk(params, device_target, source_file)
        created_file_source_disks.append(source_disk)

    return created_file_source_disks


def check_multiple_file_source_disks(params, log_config_path, test):
    """
    Check related information in libvirtd log

    :param params: wrapped parameters in dictionary
    :param log_config_path: log config path
    :param test: test assert object
    """
    msg1 = params.get('message_1', 'Setting up disks')
    msg2 = params.get('message_2', 'Setup all disks')
    for message in [msg1, msg2]:
        result = utils_misc.wait_for(lambda: libvirt.check_logfile(message, log_config_path), timeout=20)
        if not result:
            test.fail("Failed to get expected messages: %s from log file: %s."
                      % (message, log_config_path))


def run(test, params, env):
    """
    Test start Vm with device without device control file.

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test xml.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    # Disk specific attributes.
    backend_device = params.get("backend_device", "disk")

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error", "no")

    log_config_path = os.path.join(test.debugdir, "libvirtd.log")

    control_path = '/dev/mapper/control'

    disk_objects = None
    # Back up xml file.
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    # Skip this case if libvirt version doesn't support this feature
    libvirt_version.is_libvirt_feature_supported(params)
    try:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if backend_device == "multiple_disks":
            disk_objects = create_multiple_file_source_disks(params)
            if os.path.exists(control_path):
                process.run('rm -rf /dev/mapper/control', ignore_status=True, shell=True)
        if not hotplug:
            # Sync VM xml.
            for disk_xml in disk_objects:
                vmxml.add_device(disk_xml)
            vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
    except virt_vm.VMStartError as e:
        if status_error:
            if hotplug:
                test.fail("In hotplug scenario, VM should "
                          "start successfully but not."
                          "Error: %s" % str(e))
            else:
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
        test.error("unexpected exception happen: %s" % str(ex))
    else:
        if backend_device == "multiple_disks":
            check_multiple_file_source_disks(params, log_config_path, test)
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
        if not os.path.exists(control_path):
            process.run('mknod /dev/mapper/control c 10 236', ignore_status=True, shell=True)
