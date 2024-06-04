#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#
from virttest.libvirt_xml import vm_xml
from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to very the ovmf nvram elements.
    1) Prepare a guest with related nvram elements.
    2) Start and boot the guest.
    """
    vm_name = guest_os.get_vm(params)
    firmware_type = params.get("firmware_type")
    smm_state = params.get("smm_state")
    error_msg = params.get("error_msg", "")
    template_path = params.get("template_path", "")
    if template_path:
        nvram_dict = eval(params.get("nvram_dict", "{}") % template_path)
    else:
        nvram_dict = eval(params.get("nvram_dict"))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if smm_state:
            guest_os.prepare_smm_xml(vm_name, smm_state, smm_size=None)
        guest_os.prepare_os_xml(vm_name, nvram_dict, firmware_type)
        guest_os.check_vm_startup(vm, vm_name, error_msg)
    finally:
        bkxml.sync()
