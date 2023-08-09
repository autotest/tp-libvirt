#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#

import os

from virttest.libvirt_xml import vm_xml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the seabios loader elements.
    1) Prepare a guest with related os loader xml.
    2) Start and boot the guest.
    """
    vm_name = guest_os.get_vm(params)
    loader_type = params.get("loader_type", "rom")
    loader_path = params.get("loader_path")
    loader_dict = eval(params.get("loader_dict", "{}") % (loader_type, loader_path))
    error_msg = params.get("error_msg")
    incorrect_loader_path = params.get("incorrect_loader_path", "")
    use_file = "yes" == params.get("use_file", "no")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        guest_os.prepare_os_xml(vm_name, loader_dict)
        if use_file:
            fd = os.open(loader_path, os.O_CREAT)
            os.close(fd)
        guest_os.check_vm_startup(vm, vm_name, error_msg)
    finally:
        bkxml.sync()
        if use_file and os.path.exists(loader_path):
            os.remove(loader_path)
