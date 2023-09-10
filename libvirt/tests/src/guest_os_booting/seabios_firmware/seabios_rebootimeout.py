#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#

import os

from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the seabios rebootTimeout elements.
    Among them, special value -1 disables the reboot.
    1) Create a non-bootable image.
    2) Prepare a guest with rebootTimeout element.
    3) Start and boot the guest.
    """
    vm_name = guest_os.get_vm(params)
    reboot_timeout = params.get("reboot_timeout")
    bios_dict = eval(params.get("bios_dict") % reboot_timeout)
    error_msg = params.get("error_msg")
    check_prompt = params.get("check_prompt", "")
    status_error = "yes" == params.get("status_error", "no")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        # Create a non-bootable image
        disk_path = os.path.join(data_dir.get_data_dir(), "test.img")
        libvirt.create_local_disk('file', disk_path, "500M", "qcow2")
        try:
            vmxml = guest_os.prepare_os_xml(vm_name, bios_dict)
        except xcepts.LibvirtXMLError as details:
            if error_msg and error_msg in str(details):
                test.log.info("Get expected error message: %s.", error_msg)
            else:
                test.fail("Failed to define the guest because error:%s" % str(details))
            return
        # Update the guest disk to the non-bootable one
        unbootable_disk = {'source': {'attrs': {'file': disk_path}}}
        libvirt_vmxml.modify_vm_device(vmxml, 'disk', unbootable_disk)
        if not vm.is_alive() and not status_error:
            vm.start()
            vm.serial_console.read_until_any_line_matches([check_prompt], timeout=120,
                                                          internal_timeout=0.5)
            test.log.info("Get check point as expected")
    finally:
        if os.path.exists(disk_path):
            os.remove(disk_path)
        bkxml.sync()
