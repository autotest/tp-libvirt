#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import os

from avocado.utils import process

from virttest import data_dir
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.guest_os_booting import guest_os_booting_base as guest_os

# Prepare a list to record the file path which need to be removed
file_list = []


def prepare_device_attrs(test, params, vm_name, bootable_device):
    """
    Prepare the device xml based on different test matrix.

    :params test: test object
    :params params: wrapped dict with all parameters
    :params vm_name: the guest name
    :params bootable_device: the bootable device
    """
    os_dict = eval(params.get("os_dict"))
    first_dev = params.get("first_dev")
    second_dev = params.get("second_dev")
    disk_order = eval(params.get("disk_order", "{}"))
    cdrom_order = eval(params.get("cdrom_order", "{}"))
    network_order = eval(params.get("network_order", "{}"))
    disk_image = os.path.join(data_dir.get_data_dir(), 'images', 'test.img')
    vmxml = guest_os.prepare_os_xml(vm_name, os_dict)
    vmxml.remove_all_boots()
    if bootable_device != "hd_bootable":
        libvirt.create_local_disk("file", path=disk_image, size="500M", disk_format="qcow2")
        disk_dict = {'source': {'attrs': {'file': disk_image}}}
        libvirt_vmxml.modify_vm_device(vmxml, 'disk', disk_dict)
        file_list.append(disk_image)
    if bootable_device == "cdrom_bootable":
        cdrom_path = os.path.join(data_dir.get_data_dir(), 'images', 'boot.iso')
        cmd = "dnf repolist -v enabled |awk '/Repo-baseurl.*composes.*BaseOS.*os/ {res=$NF} END{print res}'"
        repo_url = process.run(cmd, shell=True).stdout_text.strip()
        boot_img_url = os.path.join(repo_url, 'images', 'boot.iso')
        if not utils_misc.wait_for(lambda: guest_os.test_file_download(boot_img_url, cdrom_path), 60):
            test.fail('Unable to download boot image')
    else:
        cdrom_path = os.path.join(data_dir.get_data_dir(), 'images', 'test.iso')
        libvirt.create_local_disk("file", path=cdrom_path, size="500M", disk_format="raw")
    file_list.append(cdrom_path)
    cdrom_dict = eval(params.get("cdrom_dict") % cdrom_path)
    cdrom_dict.update(cdrom_order)
    cdrom_xml = libvirt_vmxml.create_vm_device_by_type("disk", cdrom_dict)
    vmxml.add_device(cdrom_xml)
    vmxml.sync()
    # Update boot order attributes
    if "hd" in (first_dev, second_dev):
        libvirt_vmxml.modify_vm_device(vmxml, 'disk', disk_order)
    if "network" in (first_dev, second_dev):
        libvirt_vmxml.modify_vm_device(vmxml, 'interface', network_order)


def run(test, params, env):
    """
    This case is to verify the boot order when more than one boot dev elements in guest.
    1) Start a guest with the necessary device xml.
    2) Check the guest boot status.
    """
    vm_name = guest_os.get_vm(params)
    check_prompt = eval(params.get("check_prompt", "[]"))
    bootable_device = params.get("bootable_device")
    os_dict = eval(params.get("os_dict"))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_SETUP: prepare a guest with necessary attributes.")
        prepare_device_attrs(test, params, vm_name, bootable_device)
        test.log.info("TEST_STEP1: start the guest.")
        if not vm.is_alive():
            vm.start()
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug(f"The current guest xml is: {vmxml}")
        test.log.info("TEST_STEP2: check the guest boot from expected device.")
        if bootable_device == "hd_bootable":
            vm.wait_for_login(timeout=360).close()
            test.log.debug("Succeed to boot %s", vm_name)
        else:
            vm.serial_console.read_until_output_matches(check_prompt, timeout=600,
                                                        internal_timeout=0.5)
    finally:
        bkxml.sync()
        for file in file_list:
            if os.path.exists(file):
                os.remove(file)
