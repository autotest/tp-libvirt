#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_libvirt import libvirt_vmxml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the ovmf smm elements.
    1) Prepare a guest with related smm elements.
    2) Start and boot the guest.
    """
    vm_name = params.get("main_vm")
    smm_state = params.get("smm_state")
    smm_tseg_size = params.get("smm_tseg_size", "")
    smm_xpath = eval(params.get("smm_xpath", "{}"))
    error_msg = params.get("error_msg")
    firmware_type = params.get("firmware_type")
    loader_dict = eval(params.get("loader_dict", "{}"))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        try:
            guest_os.prepare_smm_xml(vm_name, smm_state, smm_tseg_size)
        except xcepts.LibvirtXMLError as details:
            if error_msg and error_msg in str(details):
                test.log.info("Get expected error message: %s.", error_msg)
                return
            else:
                test.fail("Failed to define the guest because error:%s", str(details))
        if smm_state == "off":
            guest_os.prepare_os_xml(vm_name, loader_dict, firmware_type)
        vmxml = guest_os.check_vm_startup(vm, vm_name, error_msg)
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, smm_xpath)
    finally:
        bkxml.sync()
