import os
import shutil
import re
import logging

from avocado.utils import process
from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest import libvirt_version
from virttest.libvirt_xml import VMXML
from virttest.utils_test import libvirt


def create_disk(params, test, vm_name, orig_iso, disk_type, target_dev, disk_format="", mode=""):
    """
    Prepare image for attach and attach it to domain

    :param parameters from cfg file
    :param vm_name : vm_name
    :param source_iso : disk's source backing file
    :param disk_type: disk's device type: cdrom or floppy
    :param target_dev: disk's target device name
    :param disk_format: disk's target format
    :param mode: readonly or shareable
    """
    slice_test = "yes" == params.get("disk_slice", "no")
    try:
        # create slice cdrom image for slice test
        if slice_test:
            libvirt.create_local_disk("file", orig_iso, size="10", disk_format=disk_format, extra="-o preallocation=full")
            params["input_source_file"] = orig_iso
            params["disk_slice"] = {"slice": "yes"}
            params["target_dev"] = "sdc"
            if mode == 'readonly':
                params["readonly"] = "yes"
            elif mode == "shareable":
                params["shareable"] = "yes"
            disk_xml = libvirt.create_disk_xml(params)
        else:
            with open(orig_iso, 'wb') as _file:
                _file.seek((1024 * 1024) - 1)
                _file.write(str(0).encode())
    except IOError:
        test.fail("Create orig_iso failed!")
    options = "--type %s --sourcetype=file --config" % disk_type
    try:
        if mode:
            options += " --mode %s" % mode
        if slice_test:
            # Use attach_device to add cdrom file with slice to guest
            virsh.attach_device(vm_name, disk_xml, flagstr="--config")
        else:
            virsh.attach_disk(vm_name, orig_iso, target_dev, options)
    except Exception:
        os.remove(orig_iso)
        test.fail("Failed to attach")


def create_attach_xml(params, test, update_xmlfile, source_iso, disk_type, target_bus,
                      target_dev, disk_mode="", disk_alias=None):
    """
    Create a xml file to update a device.

    :param parameters from cfg file
    :param update_xmlfile : path/file to save device XML
    :param source_iso : disk's source backing file.
    :param disk_type: disk's device type: cdrom or floppy
    :param target_bus: disk's target bus
    :param target_dev: disk's target device name
    :param disk_mode: readonly or shareable
    :param disk_alias: disk's alias name
    """
    slice_test = "yes" == params.get("disk_slice", "no")
    try:
        if slice_test:
            libvirt.create_local_disk("file", source_iso, size="1", disk_format="qcow2", extra="-o preallocation=full")
        else:
            with open(source_iso, 'wb') as _file:
                _file.seek((1024 * 1024) - 1)
                _file.write(str(0).encode())
    except IOError:
        test.fail("Create source_iso failed!")
    disk_class = VMXML.get_device_class('disk')
    disk = disk_class(type_name='file')
    # Static definition for comparison in check_attach()`
    disk.device = disk_type
    disk.driver = dict(name='qemu')
    disk_source = disk.new_disk_source(**{"attrs": {'file': source_iso}})
    # Add slice field involved in source
    if slice_test:
        slice_size_param = process.run("du -b %s" % source_iso).stdout_text.strip()
        slice_size = re.findall(r'[0-9]+', slice_size_param)
        slice_size = ''.join(slice_size)
        disk_source.slices = disk.new_slices(
                        **{"slice_type": "storage", "slice_offset": "0", "slice_size": slice_size})
    disk.source = disk_source
    disk.target = dict(bus=target_bus, dev=target_dev)
    if disk_mode == "readonly":
        disk.readonly = True
    if disk_mode == "shareable":
        disk.share = True
    if disk_alias:
        disk.alias = dict(name=disk_alias)
    disk.xmltreefile.write()
    shutil.copyfile(disk.xml, update_xmlfile)


def is_attached(vmxml_devices, disk_type, source_file, target_dev):
    """
    Check attached device and disk exist or not.

    :param vmxml_devices: VMXMLDevices instance
    :param disk_type: disk's device type: cdrom or floppy
    :param source_file : disk's source file to check
    :param target_dev : target device name
    :return: True/False if backing file and device found
    """
    disks = vmxml_devices.by_device_tag('disk')
    for disk in disks:
        with open(disk['xml']) as _file:
            logging.debug("Check disk XML:\n%s", _file.read())
        if disk.device != disk_type:
            continue
        if disk.target['dev'] != target_dev:
            continue
        if disk.xmltreefile.find('source') is not None:
            if disk.source.attrs['file'] != source_file:
                continue
        else:
            continue
        # All three conditions met
        logging.debug("Find %s in given disk XML", source_file)
        return True
    logging.debug("Not find %s in gievn disk XML", source_file)
    return False


def run(test, params, env):
    """
    Test command: virsh update-device.

    Update device from an XML <file>.
    1.Prepare test environment, adding a cdrom/floppy to VM.
    2.Perform virsh update-device operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    # Before doing anything - let's be sure we can support this test
    # Parse flag list, skip testing early if flag is not supported
    # NOTE: "".split("--") returns [''] which messes up later empty test
    flag = params.get("updatedevice_flag", "")
    flag_list = []
    if flag.count("--"):
        flag_list = flag.split("--")
    for item in flag_list:
        option = item.strip()
        if option == "":
            continue
        if not bool(virsh.has_command_help_match("update-device", option)):
            test.cancel("virsh update-device doesn't support --%s"
                        % option)

    # As per RH BZ 961443 avoid testing before behavior changes
    if 'config' in flag_list:
        # SKIP tests using --config if libvirt is 0.9.10 or earlier
        if not libvirt_version.version_compare(0, 9, 10):
            test.cancel("BZ 961443: --config behavior change "
                        "in version 0.9.10")
    if 'persistent' in flag_list:
        # SKIP tests using --persistent if libvirt 1.0.5 or earlier
        if not libvirt_version.version_compare(1, 0, 5):
            test.cancel("BZ 961443: --persistent behavior change "
                        "in version 1.0.5")

    # Prepare initial vm state
    vm_name = params.get("main_vm")
    vmxml = VMXML.new_from_dumpxml(vm_name, options="--inactive")
    vm = env.get_vm(vm_name)
    start_vm = "yes" == params.get("start_vm", "no")

    # Get the target bus/dev
    disk_type = params.get("disk_type", "cdrom")
    target_bus = params.get("updatedevice_target_bus", "ide")
    target_dev = params.get("updatedevice_target_dev", "hdc")
    disk_mode = params.get("disk_mode", "")
    support_mode = ['readonly', 'shareable']
    if not disk_mode and disk_mode not in support_mode:
        test.error("%s not in support mode %s"
                   % (disk_mode, support_mode))

    # Prepare tmp directory and files.
    tmp_iso_dir = data_dir.get_tmp_dir()
    orig_iso = os.path.join(tmp_iso_dir, "orig.iso")
    test_iso = os.path.join(tmp_iso_dir, "test.iso")
    test_diff_iso = os.path.join(tmp_iso_dir, "test_diff.iso")
    update_xmlfile = os.path.join(data_dir.get_tmp_dir(), "update.xml")

    # This test needs a cdrom/floppy attached first - attach a cdrom/floppy
    # to a shutdown vm. Then decide to restart or not
    if vm.is_alive():
        vm.destroy(gracefully=False)
    # Vm should be in 'shut off' status
    slice_test = params.get("disk_slice", "False")
    disk_format = params.get("driver_type")
    utils_misc.wait_for(lambda: vm.state() == "shut off", 30)
    create_disk(params, test, vm_name, orig_iso, disk_type, target_dev, disk_format, disk_mode)
    inactive_vmxml = VMXML.new_from_dumpxml(vm_name,
                                            options="--inactive")
    if start_vm:
        vm.start()
        vm.wait_for_login().close()
        domid = vm.get_id()
    else:
        domid = "domid invalid; domain is shut-off"
    if slice_test:
        disk_alias = ""
    else:
        disk_alias = libvirt.get_disk_alias(vm, orig_iso)
    create_attach_xml(params, test, update_xmlfile, test_iso, disk_type, target_bus,
                      target_dev, disk_mode, disk_alias)

    # Get remaining parameters for configuration.
    twice = "yes" == params.get("updatedevice_twice", "no")
    diff_iso = "yes" == params.get("updatedevice_diff_iso", "no")
    vm_ref = params.get("updatedevice_vm_ref", "")
    status_error = "yes" == params.get("status_error", "no")
    extra = params.get("updatedevice_extra", "")

    # OK let's give this a whirl...
    errmsg = ""
    try:
        if vm_ref == "id":
            vm_ref = domid
            if twice:
                # Don't pass in any flags
                ret = virsh.update_device(domainarg=domid, filearg=update_xmlfile,
                                          ignore_status=True, debug=True)
                if not status_error:
                    status = ret.exit_status
                    errmsg += ret.stderr
                    libvirt.check_exit_status(ret)
            if diff_iso:
                # Swap filename of device backing file in update.xml
                os.remove(update_xmlfile)
                create_attach_xml(params, test, update_xmlfile, test_diff_iso, disk_type,
                                  target_bus, target_dev, disk_mode, disk_alias)
        elif vm_ref == "uuid":
            vm_ref = vmxml.uuid
        elif vm_ref == "hex_id":
            vm_ref = hex(int(domid))
        elif vm_ref.find("updatedevice_invalid") != -1:
            vm_ref = params.get(vm_ref)
        elif vm_ref == "name":
            vm_ref = "%s %s" % (vm_name, extra)

        cmdresult = virsh.update_device(domainarg=vm_ref,
                                        filearg=update_xmlfile,
                                        flagstr=flag,
                                        ignore_status=True,
                                        debug=True)
        status = cmdresult.exit_status
        if not status_error:
            errmsg += cmdresult.stderr

        active_vmxml = VMXML.new_from_dumpxml(vm_name)
        inactive_vmxml = VMXML.new_from_dumpxml(vm_name,
                                                options="--inactive")
    finally:
        vm.destroy(gracefully=False, free_mac_addresses=False)
        vmxml.undefine()
        vmxml.restore()
        vmxml.define()
        if os.path.exists(orig_iso):
            os.remove(orig_iso)
        if os.path.exists(test_iso):
            os.remove(test_iso)
        if os.path.exists(test_diff_iso):
            os.remove(test_diff_iso)

    # Result handling logic set errmsg only on error
    if status_error:
        if status == 0:
            errmsg += "\nRun successfully with wrong command!\n"
    else:  # Normal test
        if status != 0:
            errmsg += "\nRun failed with right command\n"
        if diff_iso:  # Expect the backing file to have updated
            active_attached = is_attached(active_vmxml.devices, disk_type,
                                          test_diff_iso, target_dev)
            inactive_attached = is_attached(inactive_vmxml.devices, disk_type,
                                            test_diff_iso, target_dev)
        else:  # Expect backing file to remain the same
            active_attached = is_attached(active_vmxml.devices, disk_type,
                                          test_iso, target_dev)
            inactive_attached = is_attached(inactive_vmxml.devices, disk_type,
                                            test_iso, target_dev)

        # Check behavior of combination before individual!
        if "config" in flag_list and "live" in flag_list:
            if not active_attached:
                errmsg += ("Active domain XML not updated when "
                           "--config --live options used\n")
            if not inactive_attached:
                errmsg += ("Inactive domain XML not updated when "
                           "--config --live options used\n")

        elif "live" in flag_list and inactive_attached:
            errmsg += ("Inactive domain XML updated when "
                       "--live option used\n")

        elif "config" in flag_list and active_attached:
            errmsg += ("Active domain XML updated when "
                       "--config option used\n")

        # persistent option behavior depends on start_vm
        if "persistent" in flag_list:
            if start_vm:
                if not active_attached or not inactive_attached:
                    errmsg += ("XML not updated when --persistent "
                               "option used on active domain\n")

            else:
                if not inactive_attached:
                    errmsg += ("XML not updated when --persistent "
                               "option used on inactive domain\n")
        if len(flag_list) == 0:
            # Not specifying any flag is the same as specifying --current
            if start_vm:
                if not active_attached:
                    errmsg += "Active domain XML not updated\n"
                elif inactive_attached:
                    errmsg += ("Inactive domain XML updated when active "
                               "requested\n")

    # Log some debugging info before destroying instances
    if errmsg and not status_error:
        logging.debug("Active XML:")
        logging.debug(str(active_vmxml))
        logging.debug("Inactive XML:")
        logging.debug(str(inactive_vmxml))
        logging.debug("active_attached: %s", str(active_attached))
        logging.debug("inctive_attached: %s", str(inactive_attached))
        logging.debug("Device XML:")
        with open(update_xmlfile, "r") as _file:
            logging.debug(_file.read())

    # clean up tmp files
    del vmxml
    del active_vmxml
    del inactive_vmxml
    os.unlink(update_xmlfile)

    if errmsg:
        test.fail(errmsg)
