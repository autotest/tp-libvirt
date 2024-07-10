#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import re
import shutil

from avocado.utils import process

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import hostdev
from virttest.libvirt_xml.devices import redirdev
from virttest.utils_test import libvirt

from provider.guest_os_booting import guest_os_booting_base as guest_os
from provider.backingchain import blockcommand_base
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    This case is to verify the boot order of usb device.
    1) Prepare a guest with use device.
    2) Start the guest.
    3) Check the boot order of guest.
    """
    def get_usb_source():
        """
        Get the usb list in host based on the output from command lsusb

        :return tuple: (vendor_id, product_id) for the usb device
        """
        lsusb_list = process.run("lsusb").stdout_text.splitlines()
        test.log.info("The lsusb command result: {}".format(lsusb_list))
        found_device = False
        for line in lsusb_list:
            if re.search('hub|Controller|Keyboard|Mouse|Cdrom|Floppy', line, re.IGNORECASE):
                continue
            if len(line.split()[5].split(':')) == 2:
                vendor_id, product_id = line.split()[5].split(':')
                if not (vendor_id and product_id):
                    test.fail("vendor/product id is not available")
                found_device = True
                break
        if not found_device:
            test.fail("There's no avaiable usb device.")
        return vendor_id, product_id

    def check_boot_order():
        """
        Check the boot order of guest
        """
        current_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm.create_serial_console()
        vm.resume()
        if not any([current_xml.os.fetch_attrs().get("os_firmware") == "efi",
                    current_xml.os.fetch_attrs().get('nvram')]):
            for _ in range(3):
                vm.serial_console.sendcontrol('[')
        vm.serial_console.read_until_any_line_matches(
            [check_prompt], timeout=100, internal_timeout=5.0)

    vm_name = params.get("main_vm")
    disk_type = params.get("disk_type")
    usb_device = params.get("usb_device")
    bootmenu_dict = eval(params.get("bootmenu_dict", "{}"))
    device_attrs = eval(params.get("device_attrs", "{}"))
    port_num = params.get("port_num")
    check_prompt = params.get("check_prompt")
    required_cmds = eval(params.get("required_cmds", "[]"))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        for cmd in required_cmds:
            if not (shutil.which(cmd)):
                test.fail("Command '{}' is not available. Please install the relevant package(s)".format(cmd))
        vmxml = guest_os.prepare_os_xml(vm_name, bootmenu_dict)
        vmxml.remove_all_boots()

        if usb_device == "block_device":
            device_xml, new_image_path = disk_obj.prepare_disk_obj(disk_type, device_attrs)
        if usb_device == "redirdev_device":
            vendor_id, product_id = get_usb_source()
            device_xml = redirdev.Redirdev()
            device_xml.setup_attrs(**device_attrs)
            # start usbredirserver
            ps = process.SubProcess("usbredirserver -p {} {}:{}".format
                                    (port_num, vendor_id, product_id),
                                    shell=True)
            server_id = ps.start()
        if usb_device == "hostdev_device":
            vendor_id, product_id = get_usb_source()
            vendor_id = "0x" + vendor_id
            product_id = "0x" + product_id
            device_attrs = eval(params.get("device_attrs") % (vendor_id, product_id))
            device_xml = hostdev.Hostdev()
            device_xml.setup_attrs(**device_attrs)

        libvirt.add_vm_device(vmxml, device_xml)
        test.log.info("The current xml is {}".format(vmxml))

        if not vm.is_alive():
            virsh.start(vm_name, "--paused", debug=True, ignore_status=False)
        test.log.info("Check the boot order of guest after starting")
        check_boot_order()
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        disk_obj.cleanup_disk_preparation(disk_type)
        bkxml.sync()
        if 'server_id' in locals():
            process.run("killall usbredirserver")
