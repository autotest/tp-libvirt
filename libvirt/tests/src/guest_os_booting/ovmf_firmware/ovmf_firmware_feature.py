#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_libvirt import libvirt_vmxml
from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case it to very the ovmf firmware feature.
    1) Prepare a guest with related firmware feature.
    2) Start and boot the guest.
    """
    vm_name = guest_os.get_vm(params)
    firmware_dict = eval(params.get("firmware_dict", "{}"))
    firmware_xpath = eval(params.get("firmware_xpath", "[]"))
    error_msg = params.get("error_msg", "")
    firmware_type = params.get("firmware_type")
    status_error = "yes" == params.get("status_error", "no")
    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        try:
            guest_os.prepare_os_xml(vm_name, firmware_dict, firmware_type)
        except xcepts.LibvirtXMLError as details:
            if error_msg and error_msg in str(details):
                test.log.info("Get expected error message: %s.", error_msg)
            else:
                test.fail("Failed to define the guest because error:%s" % str(details))
        if not status_error:
            vmxml = guest_os.check_vm_startup(vm, vm_name)
            test.log.info("The active dumpxml for guest: %s" % vmxml)
            libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, firmware_xpath)
    finally:
        bkxml.sync()
