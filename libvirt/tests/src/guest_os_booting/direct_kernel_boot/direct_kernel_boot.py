#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#
import os

from avocado.utils.download import url_download

from virttest import data_dir
from virttest.libvirt_xml import vm_xml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the direct kernel boot.
    1) Prepare a guest with direct kernel boot.
    2) Start the guest.
    3) Save and restore the guest.
    """
    vm_name = params.get("main_vm")
    memory_value = int(params.get("memory_value", "2097152"))
    initrd_url = params.get("initrd_url")
    vmlinuz_url = params.get("vmlinuz_url")
    check_prompt = params.get("check_prompt")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        boot_initrd = os.path.join(data_dir.get_data_dir(), initrd_url.split("/")[-1])
        boot_vmlinuz = os.path.join(data_dir.get_data_dir(), vmlinuz_url.split("/")[-1])
        url_download(initrd_url, boot_initrd)
        url_download(vmlinuz_url, boot_vmlinuz)
        direct_kernel_dict = eval(params.get("direct_kernel_dict")
                                  % (boot_initrd, boot_vmlinuz))
        vmxml = guest_os.prepare_os_xml(vm_name, direct_kernel_dict)
        vmxml.set_memory(memory_value)
        vmxml.set_current_mem(memory_value)
        vmxml.sync()
        test.log.debug("The final guest xml is %s", vmxml)
        if not vm.is_alive():
            vm.start()
        vm.serial_console.read_until_any_line_matches([check_prompt], timeout=600)
    finally:
        bkxml.sync()
        for file_path in [boot_initrd, boot_vmlinuz]:
            if os.path.exists(file_path):
                os.remove(file_path)
