from ast import literal_eval

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml, xcepts
from virttest.utils_libvirt import libvirt_disk

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test disk with product info.

    1. Define a guest with a disk include product info.
    2. Start guest.
    3. Login guest, check the disk info.
    4. Confirm that the product info is same as xml setting.
    """
    def prepare_disk():
        """
        Prepare the disk with product info.
        """
        disk_dict = literal_eval(params.get("disk_dict", "{}"))
        expected_error = params.get("expected_error")
        status_error = "yes" == params.get("status_error", "no")
        try:
            disk_obj.add_vm_disk(disk_type, disk_dict)
        except xcepts.LibvirtXMLError as xml_error:
            if not status_error:
                test.fail(f"Failed to define VM:\n {str(xml_error)}")
            else:
                if expected_error and expected_error not in str(xml_error):
                    test.fail(f"Expected error '{expected_error}' not found in"
                              " actual error: {xml_error}")
                test.log.debug(f"Get expected error message:\n {expected_error}")
                return False
        return True

    def check_guest():
        """
        Check the disk info in guest.
        """
        vm_session = vm.wait_for_login()
        new_disk, _ = libvirt_disk.get_non_root_disk_name(vm_session)
        sg_command = "sg_inq -p di -v /dev/%s" % new_disk
        sg_output = vm_session.cmd_output(sg_command)
        if disk_product not in sg_output:
            test.fail(f"Product info '{disk_product}' not found in command output: {sg_output}")
        else:
            test.log.debug("Product info found in guest as expected.")

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    disk_type = params.get("disk_type", "file")
    disk_product = params.get("disk_product")
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        test.log.info("Start a guest with product info.")
        if not prepare_disk():
            return
        if not vm.is_alive():
            vm.start()
        test.log.debug(f"The current guest xml is: {virsh.dumpxml(vm_name).stdout_text}")
        test.log.info("Check the product info in guest.")
        check_guest()

    finally:
        if vm.is_alive():
            vm.destroy()
        backup_xml.sync()
        disk_obj.cleanup_disk_preparation(disk_type)
