import logging
import os
import random
import string

from avocado.utils import process

from virttest import libvirt_version
from virttest import virt_vm
from virttest import virsh

from virttest.libvirt_xml import vm_xml

from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

LOG = logging.getLogger('avocado.' + __name__)
cleanup_files = []


def create_customized_disk(params, alias_name, create_file=True):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :param alias_name: alias name to be set
    :param create_file: one bool indicating whether create necessary file or not
    :return: return disk device
    """
    type_name = params.get("type_name")
    device_target = params.get("target_dev")
    disk_device = params.get("device_type")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_file_path = params.get("virt_disk_device_source")
    if source_file_path:
        if create_file:
            iso_image_file = "/var/lib/libvirt/images/old_%s.img" % random.choices(string.ascii_uppercase)[0]
            process.run("dd if=/dev/urandom of=%s bs=1M count=10"
                        % iso_image_file, ignore_status=True, shell=True)
            process.run("mkisofs -o %s %s" % (source_file_path, iso_image_file),
                        ignore_status=True, shell=True)
            cleanup_files.append(iso_image_file)
        cleanup_files.append(source_file_path)

    disk_src_dict = {"attrs": {}}

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    if alias_name:
        customized_disk.alias = {"name": alias_name}
    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def test_update_to_another_alias(params, alias_name):
    """
    Test update cdrom device with new alias name

    :param params: dictionary with wrapped parameters
    :param alias_name: alias name to set
    """
    another_alias_device_obj = create_customized_disk(params, alias_name, create_file=False)
    result = virsh.update_device(params.get("main_vm"), another_alias_device_obj.xml,
                                 flagstr="--persistent", debug=True)
    update_error_message = params.get("update_error_message")
    libvirt.check_result(result, update_error_message)


def test_update_to_none_alias(params, vm, test):
    """
    Test update cdrom device with none alias

    :param params: dictionary with wrapped parameters
    :param vm: VM instance
    :param test: test object
    """
    none_alias_device_obj = create_customized_disk(params, None, create_file=False)
    # update succeed, and alias should not be in cdrom config
    virsh.update_device(params.get("main_vm"), none_alias_device_obj.xml,
                        flagstr="--persistent", ignore_status=False, debug=True)

    disk_alias_name = libvirt.get_disk_alias(vm, params.get("virt_disk_device_source"))
    if disk_alias_name is not None:
        test.fail("Find unexpected alias name: %s in disk" % disk_alias_name)
    else:
        LOG.debug("It is expected that the alias is None")


def run(test, params, env):
    """
    Test update Vm using cdrom device with alias
    And strongly related to https://bugzilla.redhat.com/show_bug.cgi?id=1603133

    1.Prepare test environment with provisioned VM
    2.Prepare test xml.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Disk specific attributes.
    alias_name = params.get("alias_name")

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")

    # Back up xml file
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Skip this case if libvirt version doesn't support this feature
    libvirt_version.is_libvirt_feature_supported(params)
    try:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Create cdrom device
        device_obj = create_customized_disk(params, alias_name)
        if not hotplug:
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
    else:
        if alias_name == "current_to_another_alias":
            test_update_to_another_alias(params, "%s_new" % alias_name)
        elif alias_name == "current_to_none_alias":
            test_update_to_none_alias(params, vm, test)
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
