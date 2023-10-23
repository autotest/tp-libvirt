#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#
import time

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the boot menu element.
    1) Prepare a guest with boot menu element.
    2) Start and boot the guest.
    """
    vm_name = guest_os.get_vm(params)
    firmware_type = params.get("firmware_type")
    bootmenu_timeout = params.get("bootmenu_timeout")
    bootmenu_enable = params.get("bootmenu_enable")
    bootmenu_dict = eval(params.get("bootmenu_dict") % (bootmenu_enable, bootmenu_timeout))
    check_prompt = params.get("check_prompt")
    error_msg = params.get("error_msg", "")
    status_error = "yes" == params.get("status_error", "no")
    directly_boot = "yes" == params.get("directly_boot", "no")
    virsh_dargs = {"debug": True, "ignore_status": True}

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        try:
            guest_os.prepare_os_xml(vm_name, bootmenu_dict)
        except xcepts.LibvirtXMLError as details:
            if error_msg and error_msg in str(details):
                test.log.info("Get expected error message: %s.", error_msg)
            else:
                test.fail("Failed to define the guest because error:%s" % str(details))
        if not status_error:
            if directly_boot:
                guest_os.check_vm_startup(vm, vm_name)
            else:
                virsh.start(vm_name, "--paused", **virsh_dargs)
                vm.create_serial_console()
                vm.resume()
                # Wait $bootmenu_timeout/1000 seconds before entering boot menu
                sleep_time = int(bootmenu_timeout) / 1000
                time.sleep(sleep_time)
                for _ in range(2):
                    vm.serial_console.sendcontrol('[')
                vm.serial_console.read_until_any_line_matches([check_prompt], timeout=30,
                                                              internal_timeout=0.5)
                test.log.info("Get check point as expected")
                if firmware_type == "seabios":
                    vm.serial_console.send('1')
                else:
                    virsh.destroy(vm_name)
                    virsh.start(vm_name, debug=True, ignore_status=False)
                vm.wait_for_login().close()
    finally:
        bkxml.sync()
