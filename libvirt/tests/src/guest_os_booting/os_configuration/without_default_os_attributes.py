#   Copyright Red Hat
#   SPDX-License-Identifier: GPL-2.0
#   Author: Meina Li <meili@redhat.com>

import platform

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify vm boot without some default os attributes.
    1) Prepare a guest xml without some default os attributes.
    2) Start the guest and check the guest status.
    3) Check the guest dumpxml.
    """
    vm_name = guest_os.get_vm(params)
    firmware_type = params.get("firmware_type")
    os_dict = eval(params.get("os_dict"))
    host_arch = platform.machine()
    os_xpath = eval(params.get("os_xpath"))

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        vmxml.del_os()
        vmxml.setup_attrs(os=os_dict)
        vmxml = guest_os.check_vm_startup(vm, vm_name)
        test.log.debug(f"The guest xml is {vmxml}")
        libvirt_vmxml.check_guest_xml_by_xpaths(vmxml, os_xpath)
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name, debug=True)
        bkxml.sync()
