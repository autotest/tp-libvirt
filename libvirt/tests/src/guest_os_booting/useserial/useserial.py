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

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the use-serial elements.
    1) Prepare a guest with bios use-serial attribute.
    2) Start the guest and pause it directly.
    3) Open the console.
    4) Resume the guest.
    5) Check the result in console.
    """
    vm_name = guest_os.get_vm(params)
    useserial = params.get("useserial")
    useserial_dict = eval(params.get("useserial_dict") % useserial)
    enable_useserial = "yes" == params.get("enable_useserial", "no")
    check_prompt = params.get("check_prompt")
    firmware_type = params.get("firmware_type")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        guest_os.prepare_os_xml(vm_name, useserial_dict)
        if firmware_type == "seabios" and enable_useserial:
            virsh.start(vm_name, "--paused", debug=True, ignore_status=False)
            vm.create_serial_console()
            time.sleep(1)
            vm.resume()
            match, text = vm.serial_console.read_until_any_line_matches([check_prompt],
                                                                        timeout=30,
                                                                        internal_timeout=0.5)
            if match == -1:
                test.log.info("Get check point as expected")
            else:
                test.fail("Can't get matched prompts!")
            vm.wait_for_login().close()
        else:
            guest_os.check_vm_startup(vm, vm_name)
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        bkxml.sync()
