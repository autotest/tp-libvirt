import re

from avocado.utils import process

from virttest import virsh

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test overall domain selinux <seclabel> can work correctly.

    1. Set VM xml and qemu.conf with proper security_driver.
    2. Start VM with proper seclabel setting and check the context.
    3. Destroy VM and check the xattr.
    """

    # Get general variables.
    chcon_img = params.get("chcon_img")
    qemu_conf = eval(params.get("qemu_conf", "{}"))
    status_error = 'yes' == params.get("status_error", 'no')

    xattr_selinux_str = params.get(
        "xattr_selinux_str",
        "trusted.libvirt.security.selinux=\"system_u:object_r:virt_image_t:s0\"")
    xattr_dac_str = params.get("xattr_dac_str", "security.ref_dac=\"1\"")
    qemu_conf_obj = None
    backup_disks = []
    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    seclabel_attr = {k.replace('seclabel_attr_', ''): int(v) if v.isdigit()
                     else v for k, v in params.items()
                     if k.startswith('seclabel_attr_')}
    seclabel_relabel = seclabel_attr.get("relabel") == "yes"

    try:
        test.log.info("TEST_STEP: Update qemu.conf.")
        qemu_conf_obj = libvirt.customize_libvirt_config(qemu_conf, "qemu")

        if chcon_img:
            for disk in list(vm.get_disk_devices().values()):
                disk_path = disk['source']
                backup_disks.append(disk_path)
                process.run(f"chcon {chcon_img} {disk_path}", shell=True)

        test.log.info("TEST_STEP: Update VM XML with %s.", seclabel_attr)
        vmxml.set_seclabel([seclabel_attr])
        vmxml.sync()
        test.log.debug(VMXML.new_from_inactive_dumpxml(vm_name))

        test.log.info("TEST_STEP: Start the VM.")
        res = virsh.start(vm.name)
        libvirt.check_exit_status(res, status_error)
        if status_error:
            return

        test.log.info("TEST_STEP: Check the xattr of the vm image.")
        vm_first_disk = libvirt_disk.get_first_disk_source(vm)
        img_xattr = libvirt_disk.get_image_xattr(vm_first_disk)
        if not re.findall(xattr_dac_str, img_xattr):
            test.fail("Unable to get %s!" % xattr_dac_str)
        if re.findall(xattr_selinux_str, img_xattr) == seclabel_relabel:
            test.fail("It should%s contain %s!"
                      % (' not' if seclabel_relabel else '', xattr_selinux_str))

        test.log.info("TEST_STEP: Destroy the VM and check the xattr of image.")
        vm.destroy(gracefully=False)
        img_xattr = libvirt_disk.get_image_xattr(vm_first_disk)
        if img_xattr:
            test.fail("The xattr output should be cleaned up after VM shutdown!")

    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        if qemu_conf_obj:
            libvirt.customize_libvirt_config(
                None, "qemu", config_object=qemu_conf_obj,
                is_recover=True)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
        for path in backup_disks:
            process.run(f"restorecon -v {path}", shell=True, verbose=True)
