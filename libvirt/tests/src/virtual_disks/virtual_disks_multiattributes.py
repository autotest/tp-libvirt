#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import os

from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_disk import disk_base


def update_bootable_disk(vm, vmxml, disk_obj, params):
    """
    Prepare a guest with multiple disk attributes.

    :params vm: vm instance
    :params vmxml: the vm xml
    :params disk_obj: the disk objective
    :params return: return the image list to delete after test
    """
    disk_type = params.get("disk_type")
    disk_dict = eval(params.get("disk_dict", "{}"))
    disk1_dict = eval(params.get("disk1_dict", "{}"))
    disk_target_dev = params.get("disk_target_dev")
    dom_iothreads = params.get("dom_iothreads")
    bootable_image = disk_obj.get_source_list(vmxml, disk_type, disk_target_dev)[0]
    libvirt_vmxml.remove_vm_devices_by_type(vm, "disk")
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    vmxml.iothreads = int(dom_iothreads)
    vmxml.sync()
    if disk_type == "file":
        cmd1 = "echo %s | sed s/qcow2/raw/g" % bootable_image
        bootable_raw_image = process.run(cmd1, ignore_status=False, shell=True).stdout_text.strip()
        cmd2 = "qemu-img convert -f qcow2 %s -O raw %s" % (bootable_image, bootable_raw_image)
        process.run(cmd2, ignore_status=False, shell=True)
        disk1_dict.update({'source': {'attrs': {'file': bootable_raw_image}}})
        disk_obj.add_vm_disk(disk_type, disk1_dict, bootable_raw_image)
        image_list.append(bootable_raw_image)
    if disk_type == "block":
        device = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                image_size='10G')
        cmd = "qemu-img convert -f qcow2 %s -O raw %s; sync" % (bootable_image, device)
        process.run(cmd, ignore_status=False, shell=True)
        disk_dict.update({'source': {'attrs': {'dev': device}}})
        disk_add = disk.Disk(disk_dict)
        disk_add.setup_attrs(**disk_dict)
        vmxml.remove_all_boots()
        vmxml.add_device(disk_add)
        vmxml.sync()
    return image_list


def add_second_disk(image_list, disk_obj, params):
    """
    Prepare a guest with multiple disk attributes.

    :params disk_obj: the disk objective
    :params image_list: get the image of bootable disk
    :params return: return the image list to delete after test
    """
    disk_type = params.get("disk_type")
    disk2_dict = eval(params.get("disk2_dict", "{}"))
    if disk_type == "file":
        new_image_path = data_dir.get_data_dir() + '/test.img'
        libvirt.create_local_disk("file", path=new_image_path, size="500M", disk_format="raw")
        disk_obj.add_vm_disk(disk_type, disk2_dict, new_image_path)
        image_list.append(new_image_path)
    return image_list


def run(test, params, env):
    """
    This case is to verify starting a guest with file/block disk which has multiple driver attributes.
    1) Prepare a guest with multiple disk attributes.
    2) Start the guest.
    3) Login the guest and read/write disk.
    """
    vm_name = params.get("main_vm")
    disk_type = params.get("disk_type")
    disk_dev = params.get("another_disk_dev")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    bkxml = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)
    global image_list
    image_list = []

    try:
        test.log.info("STEP1: prepare a guest with multiple disk attributes.")
        image_list = update_bootable_disk(vm, vmxml, disk_obj, params)
        image_list = add_second_disk(image_list, disk_obj, params)
        test.log.info("STEP2: start the guest.")
        test.log.debug("The current guest xml is: %s" % virsh.dumpxml(vm_name).stdout_text.strip())
        if not vm.is_alive():
            vm.start()
        test.log.info("STEP3: login the guest and read/write.")
        session = vm.wait_for_login()
        if disk_type == "file":
            session.cmd("mkfs.ext4 /dev/%s && mount /dev/%s /mnt" % (disk_dev, disk_dev))
        status, output = session.cmd_status_output("dd if=/dev/zero of=/mnt/file bs=1M count=100")
        if status:
            test.error("Failed to read/write in guest: %s" % output)
        else:
            test.log.debug("Read/write in guest successfully")
        session.close

    finally:
        bkxml.sync()
        if disk_type == "block":
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        if image_list:
            for image in image_list:
                if os.path.exists(image):
                    os.remove(image)
