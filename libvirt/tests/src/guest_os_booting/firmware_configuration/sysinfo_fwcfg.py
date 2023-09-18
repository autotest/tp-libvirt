#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Meina Li <meili@redhat.com>
#
import os

from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts

from provider.guest_os_booting import guest_os_booting_base as guest_os


def run(test, params, env):
    """
    This case is to verify the sysinfo fwcfg feature.
    1) Prepare a guest with sysinfo fwcfg xml.
    2) Start the guest.
    3) Check the config in guest.
    """

    def result_check():
        """
        Check the firmware config in guest
        """
        session = vm.wait_for_login()
        test_cmd = ("grep \"%s\" /sys/firmware/qemu_fw_cfg/by_name/%s/raw"
                    % (entry_value, entry_name))
        status, output = session.cmd_status_output(test_cmd)
        if status != 0:
            test.fail("Failed due to: %s" % output)
        session.close()

    vm_name = params.get("main_vm")
    entry_name = params.get("entry_name")
    entry_value = params.get("entry_value")
    file_name = params.get("file_name")
    entry_file = "yes" == params.get("entry_file", "no")
    error_msg = params.get("error_msg")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        if entry_file:
            file_path = os.path.join(data_dir.get_data_dir(), file_name)
            entry_value = "This is a new feature"
            with open(file_path, 'w') as file:
                file.write(entry_value)
            fwcfg_dict = eval(params.get("fwcfg_dict") % (entry_name, file_path))
        else:
            fwcfg_dict = eval(params.get("fwcfg_dict") % entry_name)

        vmxml.setup_attrs(sysinfo=fwcfg_dict)
        vmxml.sync()
        guest_os.check_vm_startup(vm, vm_name)
        test.log.info("The current dumpxml is %s.", vmxml)
        result_check()
    # Except condition for the vmxml.sync() step.
    except xcepts.LibvirtXMLError as xml_error:
        if error_msg and error_msg in str(xml_error):
            test.log.info("Get expected error message: %s.", error_msg)
        else:
            test.fail("Failed to define the guest because error:%s" % xml_error)

    finally:
        if vm.is_alive():
            vm.destroy(vm_name)
        if entry_file and os.path.exists(file_path):
            os.remove(file_path)
        bkxml.sync()
