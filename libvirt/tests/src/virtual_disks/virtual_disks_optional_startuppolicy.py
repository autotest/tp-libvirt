import logging

from virttest import virt_vm
from virttest import utils_disk

from virttest.libvirt_xml import vm_xml

from virttest.utils_libvirt import libvirt_disk

LOG = logging.getLogger('avocado.' + __name__)


def create_customized_disk(params, type_name, device_target, test):
    """
    Create one customized disk with related attributes

    :param params: dict wrapped with params
    :param type_nme: disk type
    :param device_target: device target e.g vdb
    :param test: test object itself
    """
    disk_device = params.get("device_type")
    device_bus = params.get("target_bus")
    device_format = params.get("target_format")
    source_dict = {}
    if type_name == "file":
        source_dict.update({"file": "/var/lib/libvirt/images/NON_EXIST"})
    elif type_name == "block":
        source_dict.update({"dev": "/dev/NON_EXIST"})
    elif type_name == "volume":
        source_dict.update({"pool": "images", "volume": "NON_EXIST"})
    else:
        test.error("Please input correct type name")
    startup_policy = params.get("startup_policy_value")
    source_dict.update({"startupPolicy": startup_policy})

    disk_src_dict = {"attrs": source_dict}

    customized_disk = libvirt_disk.create_primitive_disk_xml(
        type_name, disk_device,
        device_target, device_bus,
        device_format, disk_src_dict, None)
    LOG.debug("create customized xml: %s", customized_disk)
    return customized_disk


def check_attached_vm_disks(vm, old_parts, test):
    """
    Check related information in dmesg output

    :param vm: VM instance
    :param old_parts: VM disks before attachment
    :param test: test object
    """
    vm_session = vm.wait_for_login()
    added_parts = utils_disk.get_added_parts(vm_session, old_parts)
    vm_session.close()
    if len(added_parts) > 0:
        test.fail("Get unexpected disks: %s" % added_parts)


def run(test, params, env):
    """
    Test start Vm using file/block/volume disk types with optional startupPolicy
    And strongly related to https://bugzilla.redhat.com/show_bug.cgi?id=2095758

    1.Prepare test environment,destroy or suspend a VM.
    2.Prepare test xml.
    3.Perform test operation.
    4.Recover test environment.
    5.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Disk specific attributes.
    target_device_list = params.get("target_dev").split()
    type_name_list = params.get("type_name").split()

    status_error = "yes" == params.get("status_error")

    # Back up xml file
    if vm.is_alive():
        vm.destroy(gracefully=False)
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # this feature support on libvirt upstream after 8.5.0,
    # but rhel downstream 8.0.0
    try:
        # Setup three additional disks here
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Get old VM disk information
        if not vm.is_alive():
            vm.start()
        vm_session = vm.wait_for_login()
        old_parts = utils_disk.get_parts_list(vm_session)
        vm_session.close()
        if vm.is_alive():
            vm.destroy()
        # Create file/block/volume type disks
        for type_name, device_target in zip(type_name_list, target_device_list):
            device_obj = create_customized_disk(params, type_name, device_target, test)
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
        check_attached_vm_disks(vm, old_parts, test)
    finally:
        # Recover VM
        LOG.info("Restoring vm...")
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
