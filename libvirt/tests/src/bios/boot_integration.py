import time
import logging
import os
import re

from virttest import virsh
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import xcepts
from virttest.utils_libvirt import libvirt_disk
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


def vmxml_recover_from_snap(vm_name, boot_type, vmxml, test):
    """
    Recover old xml config with backup xml.

    :params: vm_name: the vm name
    :params: boot_type: the type of booting guest
    :params: vmxml:VMXML object
    :params: test: system parameter
    """
    try:
        if boot_type == "seabios":
            options = "--snapshots-metadata"
        else:
            options = "--snapshots-metadata --nvram"
        vmxml.sync(options)
        logging.debug("Check snapshot config file")
        path = "/var/lib/libvirt/qemu/snapshot/" + vm_name
        if os.path.isfile(path):
            test.fail("Still can find snapshot metadata")

    except xcepts.LibvirtXMLError as detail:
        logging.error("Recover older xml failed: %s.", detail)


def snapshot_list_check(vm_name, snapshot_take, postfix, test):
    """"
    Create snapshot for guest with boot dev or boot order

    :params: vm_name: the name of the vm
    :params: snap_take: the number of snapshots
    :params: postfix: the postfix of snapshot name
    :params: test: system parameter
    """

    # Check the snapshot list
    get_sname = []
    for count in range(1, snapshot_take + 1):
        get_sname.append(("%s_%s") % (postfix, count))
    snaps_list = virsh.snapshot_list(vm_name, debug=True)
    find = 0
    for snap in snaps_list:
        if snap in get_sname:
            find += 1
    if find != snapshot_take:
        test.fail("Can not find all snapshots %s!" % get_sname)


def domain_lifecycle(vmxml, vm, test, virsh_dargs, **kwargs):
    """
    Test the lifecycle of the domain

    :params: vmxml: the xml of the vm
    :params: vm: vm to wait for login the vm
    :params: test: system parameter
    :params: virsh_dargs: the debugging status of virsh command
    """
    vm_name = kwargs.get("vm_name")
    save_file = kwargs.get("save_file")
    boot_type = kwargs.get("boot_type")
    nvram_file = kwargs.get("nvram_file")

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


def run(test, params, env):
    """
    1) Test Define/undefine/start/destroy/save/restore a OVMF/Seabios domain
    with 'boot dev' element or 'boot order' element
    2) Test Create snapshot with 'boot dev' or 'boot order'

    Steps:
    1) Prepare a typical VM XML, e.g. for OVMF or Seabios Guest boot
    2) Setup boot sequence by element 'boot dev' or 'boot order'
    3) Define/undefine/start/destroy/save/restore VM and check result
    4) Create snapshot with 'boot dev' or 'boot order'
    """
    vm_name = params.get("main_vm", "")
    vm = env.get_vm(vm_name)
    boot_type = params.get("boot_type", "seabios")
    boot_ref = params.get("boot_ref", "dev")
    disk_target_dev = params.get("disk_target_dev", "")
    disk_target_bus = params.get("disk_target_bus", "")
    tmp_file = data_dir.get_data_dir()
    save_file = os.path.join(tmp_file, vm_name + ".save")
    nvram_file = params.get("nvram", "")
    expected_text = params.get("expected_text", None)
    boot_entry = params.get("boot_entry", None)
    with_snapshot = "yes" == params.get("with_snapshot", "no")
    snapshot_take = int(params.get("snapshot_take", "1"))
    postfix = params.get("postfix", "")

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
            if with_snapshot:
                # Create snapshot for guest with boot dev or boot order
                virsh.define(vmxml.xml, **virsh_dargs)
                virsh.start(vm_name, **virsh_dargs)
                vm.wait_for_login()
                external_snapshot = libvirt_disk.make_external_disk_snapshots(vm, disk_target_dev, postfix, snapshot_take)
                snapshot_list_check(vm_name, snapshot_take, postfix, test)
            else:
                # Test the lifecycle for the guest
                kwargs = {"vm_name": vm_name,
                          "save_file": save_file,
                          "boot_type": boot_type,
                          "nvram_file": nvram_file}
                domain_lifecycle(vmxml, vm, test, virsh_dargs, **kwargs)
    finally:
        logging.debug("Start to cleanup")
        if vm.is_alive:
            vm.destroy()
        logging.debug("Restore the VM XML")
        if os.path.exists(save_file):
            os.remove(save_file)
        if with_snapshot:
            vmxml_recover_from_snap(vm_name, boot_type, vmxml_backup, test)
            # Remove the generated snapshot file
            for snap_file in external_snapshot:
                if os.path.exists(snap_file):
                    os.remove(snap_file)
        else:
            vmxml_backup.sync()
