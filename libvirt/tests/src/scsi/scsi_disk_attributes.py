from avocado.utils import process

from virttest import virsh
from virttest import utils_disk
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml, libvirt_disk
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    This case is to verify starting the guest with scsi disk with different attributes.
    """
    def check_result():
        """
        Check the result in host and guest.
        """
        test.log.debug("The current guest xml is %s", virsh.dumpxml(vm_name).stdout_text)
        test.log.info("TEST_STEP2: check the guest xml.")
        xml_after_adding_device = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(xml_after_adding_device, expected_xpaths)
        test.log.info("TEST_STEP3: check the result in host and guest.")
        vm_session = vm.wait_for_login()
        cap_result = process.run("getpcaps `pidof qemu-kvm`", shell=True).stdout_text.strip()
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        utils_disk.dd_data_to_vm_disk(vm_session, new_disk)
        _, sg_result = vm_session.cmd_status_output("sg_persist -v /dev/%s" % new_disk)
        vm_session.close()
        if rawio_value == "yes":
            if "cap_sys_rawio" not in cap_result or "registered reservation keys" not in sg_result:
                test.fail("The rawio capability doesn't work as expected.")
        if rawio_value == "no":
            if "cap_sys_rawio" in cap_result or "aborted command" not in sg_result.lower():
                test.fail("Shouldn't get the rawio capability.")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    disk_type = params.get("disk_type")
    rawio_value = params.get("rawio_value")
    expected_xpaths = eval(params.get("expected_xpaths"))

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    try:
        test.log.info("TEST_STEP1: start the guest with scsi disk.")
        device_name = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        disk_dict = eval(params.get("disk_dict", "{}") % device_name)
        disk_obj = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
        libvirt.add_vm_device(vmxml, disk_obj)
        if not vm.is_alive():
            vm.start()
        check_result()
    finally:
        backup_xml.sync()
        libvirt.setup_or_cleanup_iscsi(is_setup=False)
