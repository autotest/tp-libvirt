#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import os

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.filesystem import Filesystem
from virttest.utils_libvirt import libvirt_vmxml

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    This case is to verify hotplugging device with boot order element.
    1) Prepare a running guest.
    2) Hotplug the device with boot order element.
    3) Check the dumpxml.
    4) Hot-unplug the device.
    """
    def prepare_device_xml(vm_xml, device_type):
        """
        Prepare the hot-plugged device xml.

        :params vm_xml: the instance of VMXML class
        :params device_type: the device type
        :return: tuple, (device_xml, image_path) for the attached device
        """
        image_path = ''
        # Need to use shared memory for filesystem device
        if device_type == "filesystem_device":
            vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared",
                                               hpgs=False)
            device_xml = Filesystem()
            device_xml.setup_attrs(**device_dict)
        else:
            device_xml, image_path = disk_obj.prepare_disk_obj("file", device_dict)
        return device_xml, image_path

    def check_dumpxml(device_type, image_path, exist):
        """
        Check if the device is existed and included the boot order element.

        :params device_type: the device type
        :params image_path: the disk path
        :params exist: whether the device is existed
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug(f"The current guest xml is: {vmxml}")
        if device_type == "filesystem_device":
            device_status = vmxml.devices.by_device_tag('filesystem')
        else:
            device_status = vm_xml.VMXML.check_disk_exist(vm_name, image_path)
        if exist:
            if not device_status:
                test.fail(f"No {device_type} in guest xml after hotplug.")
            # To make sure the boot order is also in device xml
            libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, order_xpath, ignore_status=False)
        else:
            if device_status:
                test.fail(f"The {device_type} isn't detached successfully.")

    vm_name = params.get("main_vm")
    device_type = params.get("device_type")
    target_dev = params.get("target_dev")
    boot_order = params.get("boot_order", "1")
    device_dict = eval(params.get("device_dict"))
    order_xpath = eval(params.get("order_xpath"))
    virsh_dargs = {'debug': True, 'ignore_status': False}

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)

    try:
        test.log.info("STEP1: Prepare a guest with boot order.")
        vmxml.remove_all_boots()
        vmxml.set_boot_order_by_target_dev(target_dev, order=boot_order)
        vmxml.sync()

        test.log.info(f"STEP2: Hotplug the {device_type}.")
        device_xml, image_path = prepare_device_xml(vm_xml, device_type)
        if not vm.is_alive():
            virsh.start(vm_name, **virsh_dargs)
        vm.wait_for_login()
        virsh.attach_device(vm_name, device_xml.xml, **virsh_dargs)
        check_dumpxml(device_type, image_path, exist=True)

        test.log.info(f"STEP3: Hot-unplug the {device_type}.")
        virsh.detach_device(vm_name, device_xml.xml, wait_for_event=True, **virsh_dargs)
        check_dumpxml(device_type, image_path, exist=False)
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name, **virsh_dargs)
        if image_path and os.path.exists(image_path):
            os.remove(image_path)
        bkxml.sync()
