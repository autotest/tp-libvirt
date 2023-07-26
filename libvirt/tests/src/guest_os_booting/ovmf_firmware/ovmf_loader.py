#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#

import os

from virttest import libvirt_version
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the ovmf loader elements.
    1) Prepare a guest with related os loader xml.
    2) Start and boot the guest.
    """
    vm_name = params.get("main_vm")
    firmware_type = params.get("firmware_type")
    loader_dict = eval(params.get("loader_dict", "{}"))
    loader_xpath = eval(params.get("loader_xpath", "[]"))
    smm_state = params.get("smm_state", "off")
    error_msg = params.get("error_msg")
    incorrect_loader_path = params.get("incorrect_loader_path", "")
    use_file = "yes" == params.get("use_file", "no")
    stateless = "yes" == params.get("stateless", "no")
    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        guest_os.prepare_smm_xml(vm_name, smm_state, "")
        vmxml = guest_os.prepare_os_xml(vm_name, loader_dict, firmware_type)
        # stateless='yes' only use for AMD test, so here we only check the dumpxml for it to avoid the machine issue
        if stateless:
            virsh.start(vm_name, debug=True)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, loader_xpath)
        else:
            if use_file:
                fd = os.open(incorrect_loader_path, os.O_CREAT)
                os.close(fd)
            guest_os.check_vm_startup(vm, vm_name, error_msg)
    finally:
        bkxml.sync()
        if os.path.exists(incorrect_loader_path):
            os.remove(incorrect_loader_path)
