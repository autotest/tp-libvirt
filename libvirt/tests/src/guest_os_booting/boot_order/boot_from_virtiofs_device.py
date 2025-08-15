#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import os
import re
import shutil

from avocado.utils import process

from virttest import data_dir
from virttest import utils_selinux
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import filesystem
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.guest_os_booting import guest_os_booting_base as guest_os

BOOT_PATH = data_dir.get_data_dir()


def run(test, params, env):
    """
    This case is to verify to boot from virtiofs device.
    1) Prepare a virtiofs boot environment.
    2) Start a guest with virtiofs related xml.
    3) Login guest to check.
    """
    def prepare_virtiofs_bootable_system():
        """
        Prepare a basic linux root file system.
        """
        cmd1 = f"mkdir {install_root}"
        cmd2 = f"dnf --installroot={install_root} --releasever=9 install "\
               "system-release vim-minimal systemd passwd dnf rootfiles sudo "\
               "kernel kernel-modules net-tools yum -y >/dev/null"
        # Create the initramfs.
        cmd3 = f"dracut {initrams_file} --early-microcode "\
               "--add virtiofs --filesystem virtiofs"
        multi_cmd = f"{cmd1}; {cmd2}; {cmd3}"
        process.run(multi_cmd, shell=True, ignore_status=False)
        change_virtiofs_root_passwd()
        # Copy vmlinuz from host to use in guest.
        cmd5 = f"cp $(find /boot -name 'vmlinuz*' | tail -n 1) {vmlinuz_file}"
        process.run(cmd5, shell=True)

    def change_virtiofs_root_passwd():
        """
        After install virtiofs related linux root file system, the password of root
        need to be changed
        """
        selinux_mode = utils_selinux.get_status()
        utils_selinux.set_status("permissive")
        fd = os.open('/', os.R_OK, os.X_OK)
        os.chroot(install_root)
        os.chdir('/')
        set_passwd_cmd = f"echo {passwd} | passwd --stdin {username} &>/dev/null; exit"
        os.system(set_passwd_cmd)
        os.fchdir(fd)
        os.chroot('.')
        test.log.info("The passwd has been changed and exit the chroot env.")
        utils_selinux.set_status(selinux_mode)

    vm_name = params.get("main_vm")
    install_root = os.path.join(BOOT_PATH, "virtio-fs-root")
    initrams_file = os.path.join(BOOT_PATH, "initramfs-virtiofs.img")
    vmlinuz_file = os.path.join(BOOT_PATH, "vmlinuz-virtiofs.img")
    boot_img = os.path.join(data_dir.get_data_dir(), "test.img")
    guest_cmd = params.get("guest_cmd")
    target_dir = params.get("target_dir")
    vm_memory = int(params.get("vm_memory"))
    username = params.get("username")
    passwd = params.get("password")
    disk_dict = {}

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        test.log.info("SETUP: Prepare a virtiofs bootable environment.")
        prepare_virtiofs_bootable_system()
        os_dict = eval(params.get("os_dict") % (initrams_file, vmlinuz_file))
        virtiofs_dict = eval(params.get("virtiofs_dict") % install_root)

        test.log.info("STEP1: Prepare a guest xml with memory and virtiofs.")
        vm_xml.VMXML.set_memoryBacking_tag(vm_name, access_mode="shared", hpgs=False)
        vmxml = guest_os.prepare_os_xml(vm_name, os_dict)
        test.log.info("Better to use big enough memory size, for example 15728640.")
        vmxml.memory = vm_memory
        vmxml.remove_all_boots()
        virtiofs_dev = filesystem.Filesystem()
        virtiofs_dev.setup_attrs(**virtiofs_dict)
        test.log.debug(f"The filesystem device xml is {virtiofs_dev}")
        vmxml.add_device(virtiofs_dev)

        test.log.info("STEP2: Start the guest with non-bootable disk image.")
        libvirt.create_local_disk("file", path=boot_img, size="10G", disk_format="qcow2")
        disk_dict.update({'source': {'attrs': {'file': boot_img}}})
        libvirt_vmxml.modify_vm_device(vmxml, 'disk', disk_dict)
        if not vm.is_alive():
            vm.start()
        test.log.info("STEP3: Login the guest")
        vm_session = vm.wait_for_serial_login()
        result = vm_session.cmd_output(guest_cmd)
        test.log.debug("Send cmd: '%s' in console", guest_cmd)
        if not re.search(target_dir, result):
            test.fail(f"Expect {target_dir} in {result}, but not found")
        else:
            test.log.debug(f"Got {target_dir} in {result} as expected")

    finally:
        if vm.is_alive():
            virsh.destroy(vm_name, debug=True, ignore_status=False)
        test.log.info("Remove the linux root file system directory.")
        if os.path.exists(install_root):
            shutil.rmtree(install_root)
        for file_path in [initrams_file, vmlinuz_file, boot_img]:
            if os.path.exists(file_path):
                os.remove(file_path)
        bkxml.sync()
