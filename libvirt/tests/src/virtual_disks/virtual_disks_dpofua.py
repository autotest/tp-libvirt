import ast

from virttest import libvirt_version
from virttest import utils_disk
from virttest import virsh
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test disk dpofua attribute
    """
    def setup_guest():
        """
        Prepare the guest with dpofua disk
        """
        if with_hotplug:
            test.log.info("SETUP_STEP:Prepare hotplugged disk xml with dpofua disk.")
            _, new_image_path = disk_obj.prepare_disk_obj(disk_type, disk_dict)
            disk_dict.update({'source': {'attrs': {'file': new_image_path}}})
            disk_dev = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
            test.log.debug("The disk xml is:\n%s", disk_dev)
            return disk_dev
        else:
            test.log.info("SETUP_STEP: Prepare the guest xml with dpofua disk.")
            disk_obj.add_vm_disk(disk_type, disk_dict)
            return None

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    with_hotplug = params.get_boolean("with_hotplug", False)
    disk_type = params.get("disk_type")
    disk_dict = ast.literal_eval(params.get("disk_dict", "{}"))

    dpofua_value = params.get("dpofua_value")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        test.log.info("TEST_STEP: Start the guest.")
        disk_dev = setup_guest()
        if not vm.is_alive():
            vm.start()
        test.log.debug("The current vm xml is:\n%s", virsh.dumpxml(vm_name).stdout_text)
        vm_session = vm.wait_for_login()
        if with_hotplug:
            test.log.info("TEST_STEP: Hotplug the disk with dpofua='%s'", dpofua_value)
            virsh.attach_device(vm_name, disk_dev.xml, debug=True,
                                wait_for_event=True, ignore_status=False)

        test.log.info("TEST_STEP: Check the dpofua attribute in guest.")
        expect_xml_line = 'dpofua="%s"' % dpofua_value
        if not utils_misc.wait_for(lambda: libvirt.check_dumpxml(vm, expect_xml_line, err_ignore=True), timeout=5):
            test.fail("The dpofua attribute is not found in vm xml after hotplugging the disk.")

        test.log.info("TEST_STEP: Write data to the disk in guest.")
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        utils_disk.dd_data_to_vm_disk(vm_session, "/dev/%s" % new_disk)
        vm_session.close()

        if not with_hotplug:
            test.log.info("TEST_STEP: Check the QEMU command line.")
            check_qemu_pattern = '"dpofua":true' if dpofua_value == 'on' else '"dpofua":false'
            libvirt.check_qemu_cmd_line(check_qemu_pattern)
        else:
            virsh.detach_device(vm_name, disk_dev.xml, debug=True,
                                wait_for_event=True, ignore_status=False)
            if libvirt.check_dumpxml(vm, expect_xml_line, err_ignore=True):
                test.fail("The dpofua attribute is still present after detaching the disk.")
    finally:
        if vm.is_alive():
            vm.destroy()
        bkxml.sync()
        disk_obj.cleanup_disk_preparation(disk_type)
