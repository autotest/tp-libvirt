import re

from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Check the xattr of resource file when guest fails to start.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    error_msg = params.get("error_msg")
    xattr_selinux_str = params.get("xattr_selinux_str",
                                   "trusted.libvirt.security.ref_selinux=\"1\"")
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    disk_path = vm.get_first_disk_devices()['source']
    vm2_name = vm.name + '_' + utils_misc.generate_random_string(3)
    try:
        test.log.info("TEST_STEP: Start a VM and check the xattr of disk image.")
        vm.start()
        img_xattr = libvirt_disk.get_image_xattr(disk_path)
        if not re.findall(xattr_selinux_str, img_xattr):
            test.fail("Unable to get %s!" % xattr_selinux_str)

        test.log.info("TEST_STEP: Start another VM using the same image with "
                      "the first VM.")
        vmxml2 = VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml2.setup_attrs(**{'vm_name': vm2_name})
        vmxml2.del_uuid()
        virsh.define(vmxml2.xml, debug=True, ignore_status=False)

        result = virsh.start(vm2_name, debug=True)
        libvirt.check_result(result, error_msg)

        test.log.info("TEST_STEP: Check the xattr of disk image")
        img_xattr = libvirt_disk.get_image_xattr(disk_path)
        if not re.findall(xattr_selinux_str, img_xattr):
            test.fail("Unable to get %s!" % xattr_selinux_str)

    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        backup_xml.sync()
        if 'vmxml2' in locals():
            vmxml2.undefine()
