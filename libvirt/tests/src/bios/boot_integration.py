import time
import logging
import os
import re

from virttest import virsh
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv
from virttest.utils_misc import wait_for
from virttest import data_dir

from virttest import libvirt_version


def prepare_boot_xml(vmxml, params):
    """
    Prepare a normal xml for Uefi and Seabios guest boot

    :param vmxml: The instance of VMXML class
    :param params: Avocado params object
    """
    # Boot options
    loader_type = params.get("loader_type", "")
    loader = params.get("loader", "")
    boot_type = params.get("boot_type", "seabios")
    readonly = params.get("readonly", "")
    bootmenu_enable = params.get("bootmenu_enable", "")
    bootmenu_timeout = params.get("bootmenu_timeout", "")
    smbios_mode = params.get("smbios_mode", "")

    dict_os_attrs = {}

    logging.debug("Set boot loader common attributes")
    dict_os_attrs.update({"bootmenu_enable": bootmenu_enable})
    dict_os_attrs.update({"bootmenu_timeout": bootmenu_timeout})
    if loader:
        dict_os_attrs.update({"loader": loader})
        dict_os_attrs.update({"loader_type": loader_type})
        dict_os_attrs.update({"loader_readonly": readonly})
    if smbios_mode:
        dict_os_attrs.update({"smbios_mode": smbios_mode})

    # Set Uefi special attributes
    if boot_type == "ovmf":
        logging.debug("Set Uefi special attributes")
        nvram = params.get("nvram", "")
        nvram_template = params.get("template", "")
        dict_os_attrs.update({"nvram": nvram})
        dict_os_attrs.update({"nvram_template": nvram_template})

    # Set Seabios special attributes
    if boot_type == "seabios":
        logging.debug("Set Seabios special attributes")
        bios_useserial = params.get("bios_useserial", "")
        bios_reboot_timeout = params.get("bios_reboot_timeout", "")
        dict_os_attrs.update({"bios_useserial": bios_useserial})
        dict_os_attrs.update({"bios_reboot_timeout": bios_reboot_timeout})

    vmxml.set_os_attrs(**dict_os_attrs)
    return vmxml


def console_check(vm, pattern, debug_log=False):
    """
    Return function for use with wait_for.

    :param vm: vm to check serial console for
    :param pattern: line or pattern to match
    :param debug_log: if to log all output for debugging
    :return: function returning true if console output matches pattern
    """
    def _matches():
        output = vm.serial_console.get_stripped_output()
        matches = re.search(pattern, output, re.S)
        if debug_log:
            logging.debug("Checked for '%s' in '%s'", pattern, output)
        if matches:
            logging.debug("Found '%s' in '%s'", pattern, output)
            return True
        return False
    return _matches


def run(test, params, env):
    """
    Test Define/undefine/start/destroy/save/restore a OVMF/Seabios domain
    with 'boot dev' element or 'boot order' element

    Steps:
    1) Prepare a typical VM XML, e.g. for OVMF or Seabios Guest boot
    2) Setup boot sequence by element 'boot dev' or 'boot order'
    2) Define/undefine/start/destroy/save/restore VM and check result
    """
    vm_name = params.get("main_vm", "")
    vm = env.get_vm(vm_name)
    status_error = "yes" == params.get("status_error", "no")
    boot_type = params.get("boot_type", "seabios")
    boot_ref = params.get("boot_ref", "dev")
    disk_target_dev = params.get("disk_target_dev", "")
    disk_target_bus = params.get("disk_target_bus", "")
    save_file = os.path.join(data_dir.get_tmp_dir(), vm_name + ".save")
    nvram_file = params.get("nvram", "")
    expected_text = params.get("expected_text", None)
    boot_entry = params.get("boot_entry", None)

    # Back VM XML
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    if boot_type == "ovmf":
        if not libvirt_version.version_compare(2, 0, 0):
            test.error("OVMF doesn't support in current"
                       " libvirt version.")

        if not utils_package.package_install('OVMF'):
            test.error("OVMF package install failed")

    if (boot_type == "seabios" and
            not utils_package.package_install('seabios-bin')):
        test.error("seabios package install failed")

    try:
        prepare_boot_xml(vmxml, params)

        # Update domain disk
        # Use sata for uefi, and ide for seabios
        domain_disk = vmxml.get_devices(device_type='disk')[0]
        domain_disk.target = {"dev": disk_target_dev, "bus": disk_target_bus}
        del domain_disk['address']
        vmxml.remove_all_disk()
        vmxml.add_device(domain_disk)

        # Setup boot start sequence
        vmxml.remove_all_boots()
        if boot_ref == "order":
            vmxml.set_boot_order_by_target_dev(disk_target_dev, "1")
        if boot_ref == "dev":
            vmxml.set_os_attrs(**{"boots": ["hd"]})

        logging.debug("The new VM XML is:\n%s", vmxml)
        vmxml.undefine()

        virsh_dargs = {"debug": True, "ignore_status": True}

        if boot_type == "s390_qemu":
            # Start test and check result
            ret = virsh.define(vmxml.xml, **virsh_dargs)
            ret = virsh.start(vm_name, "--paused", **virsh_dargs)
            time.sleep(1)
            vm.create_serial_console()
            time.sleep(1)
            vm.resume()
            if not boot_entry:
                check_boot = console_check(vm, expected_text)
                if not wait_for(check_boot, 60, 1):
                    test.fail("No boot menu found. Please check log.")
            else:
                vm.serial_console.send(boot_entry)
                time.sleep(0.5)
                vm.serial_console.sendcontrol('m')
                check_boot = console_check(vm, expected_text)
                if not wait_for(check_boot, 60, 1):
                    test.fail("Boot entry not selected. Please check log.")
                vm.wait_for_login()
        else:
            # Start test and check result
            ret = virsh.define(vmxml.xml, **virsh_dargs)
            stdout_patt = "Domain .*%s.* defined from %s" % (vm_name, vmxml.xml)
            utlv.check_result(ret, expected_match=[stdout_patt])

            ret = virsh.start(vm_name, **virsh_dargs)
            stdout_patt = "Domain .*%s.* started" % vm_name
            utlv.check_result(ret, expected_match=[stdout_patt])
            vm.wait_for_login()

            ret = virsh.destroy(vm_name, **virsh_dargs)
            stdout_patt = "Domain .*%s.* destroyed" % vm_name
            utlv.check_result(ret, expected_match=[stdout_patt])

            vm.start()
            ret = virsh.save(vm_name, save_file, **virsh_dargs)
            stdout_patt = "Domain .*%s.* saved to %s" % (vm_name, save_file)
            utlv.check_result(ret, expected_match=[stdout_patt])

            ret = virsh.restore(save_file, **virsh_dargs)
            stdout_patt = "Domain restored from %s" % save_file
            utlv.check_result(ret, expected_match=[stdout_patt])

            ret = virsh.undefine(vm_name, options="--nvram", **virsh_dargs)
            stdout_patt = "Domain .*%s.* has been undefined" % vm_name
            utlv.check_result(ret, expected_match=[stdout_patt])
            if boot_type == "ovmf":
                if os.path.exists(nvram_file):
                    test.fail("nvram file still exists after vm undefine")
    finally:
        logging.debug("Start to cleanup")
        if vm.is_alive:
            vm.destroy()
        logging.debug("Restore the VM XML")
        vmxml_backup.sync()
