import re
import os
import shutil
import logging
from autotest.client.shared import error
from virttest import virsh
from virttest.libvirt_xml import VMXML
from provider import libvirt_version


def create_cdrom(vm_name, orig_iso, target_dev):
    """
    :param vm_name : vm_name
    :param source_iso : disk's source backing file.
    """
    try:
        _file = open(orig_iso, 'wb')
        _file.seek((1024 * 1024) - 1)
        _file.write(str(0))
        _file.close()
    except IOError:
        raise error.TestFail("Create orig_iso failed!")
    try:
        virsh.attach_disk(vm_name, orig_iso, target_dev,
                          "--type cdrom --sourcetype=file --config")
    except:
        os.remote(orig_iso)
        raise error.TestFail("Failed to attach")


def create_attach_xml(update_xmlfile, source_iso, target_bus, target_dev):
    """
    Create a xml file to update a device.

    :param update_xmlfile : path/file to save device XML
    :param source_iso : disk's source backing file.
    :param target_bus : disk's target bus
    :param target_dev : disk's target device name
    """
    try:
        _file = open(source_iso, 'wb')
        _file.seek((1024 * 1024) - 1)
        _file.write(str(0))
        _file.close()
    except IOError:
        raise error.TestFail("Create source_iso failed!")
    disk_class = VMXML.get_device_class('disk')
    disk = disk_class(type_name='file')
    # Static definition for comparison in check_attach()
    disk.device = 'cdrom'
    disk.driver = dict(name='file')
    disk.source = disk.new_disk_source(attrs={'file': source_iso})
    disk.target = dict(bus=target_bus, dev=target_dev)
    disk.readonly = True
    disk.xmltreefile.write()
    shutil.copyfile(disk.xml, update_xmlfile)


def is_attached(vmxml_devices, source_file, target_dev):
    """
    Check attached device and disk exist or not.

    :param source_file : disk's source file to check
    :param vmxml_devices: VMXMLDevices instance
    :param target_dev : target device name
    :return: True/False if backing file and device found
    """
    disks = vmxml_devices.by_device_tag('disk')
    for disk in disks:
        if disk.device != 'cdrom':
            continue
        if disk.target['dev'] != target_dev:
            continue
        if disk.source.attrs['file'] != source_file:
            continue
        # All three conditions met
        return True
    logging.error('No cdrom device for "%s" in devices XML: "%s"',
                  source_file, vmxml_devices)
    return False


def run(test, params, env):
    """
    Test command: virsh update-device.

    Update device from an XML <file>.
    1.Prepare test environment, adding a cdrom to VM.
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
            raise error.TestNAError("virsh update-device doesn't support --%s"
                                    % option)

    # As per RH BZ 961443 avoid testing before behavior changes
    if 'config' in flag_list:
        # SKIP tests using --config if libvirt is 0.9.10 or earlier
        if not libvirt_version.LibVersionCompare(0, 9, 10):
            raise error.TestNAError("BZ 961443: --config behavior change "
                                    "in version 0.9.10")
    if 'persistent' in flag_list:
        # SKIP tests using --persistent if libvirt 1.0.5 or earlier
        if not libvirt_version.LibVersionCompare(1, 0, 5):
            raise error.TestNAError("BZ 961443: --persistent behavior change "
                                    "in version 1.0.5")

    # Prepare initial vm state
    vm_name = params.get("main_vm")
    vmxml = VMXML.new_from_dumpxml(vm_name, options="--inactive")
    vm = env.get_vm(vm_name)
    start_vm = "yes" == params.get("start_vm", "no")

    # Define target bus/dev (these could be params some day to
    # test virtio/vdc for example)
    target_bus = "ide"
    target_dev = "hdc"

    # Prepare tmp directory and files.
    orig_iso = os.path.join(test.virtdir, "orig.iso")
    test_iso = os.path.join(test.virtdir, "test.iso")
    test_diff_iso = os.path.join(test.virtdir, "test_diff.iso")
    update_xmlfile = os.path.join(test.tmpdir, "update.xml")
    create_attach_xml(update_xmlfile, test_iso, target_bus, target_dev)

    # This test needs a cdrom attached first - attach a cdrom
    # to a shutdown vm. Then decide to restart or not
    if vm.is_alive():
        vm.destroy()
    create_cdrom(vm_name, orig_iso, target_dev)
    if start_vm:
        vm.start()
        domid = vm.get_id()
    else:
        domid = "domid invalid; domain is shut-off"

    # Get remaining parameters for configuration.
    twice = "yes" == params.get("updatedevice_twice", "no")
    diff_iso = "yes" == params.get("updatedevice_diff_iso", "no")
    vm_ref = params.get("updatedevice_vm_ref", "")
    status_error = "yes" == params.get("status_error", "no")
    extra = params.get("updatedevice_extra", "")

    # OK let's give this a whirl...
    try:
        if vm_ref == "id":
            vm_ref = domid
            if twice:
                # Don't pass in any flags
                virsh.update_device(domainarg=domid, filearg=update_xmlfile,
                                    ignore_status=True, debug=True)
            if diff_iso:
                # Swap filename of device backing file in update.xml
                os.remove(update_xmlfile)
                create_attach_xml(update_xmlfile, test_diff_iso,
                                  target_bus, target_dev)
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
    errmsg = None
    if status_error:
        if status == 0:
            errmsg = "Run successfully with wrong command!"
    else:  # Normal test
        if status != 0:
            errmsg = "Run failed with right command"
        if diff_iso:  # Expect the backing file to have updated
            active_attached = is_attached(active_vmxml.devices,
                                          test_diff_iso, target_dev)
            inactive_attached = is_attached(inactive_vmxml.devices,
                                            test_diff_iso, target_dev)
        else:  # Expect backing file to remain the same
            active_attached = is_attached(active_vmxml.devices, test_iso,
                                          target_dev)
            inactive_attached = is_attached(inactive_vmxml.devices, test_iso,
                                            target_dev)

        # Check behavior of combination before individual!
        if "config" in flag_list and "live" in flag_list:
            if not active_attached:
                errmsg = ("Active domain XML not updated when "
                          "--config --live options used")
            if not inactive_attached:
                errmsg = ("Inactive domain XML not updated when "
                          "--config --live options used")

        elif "live" in flag_list and inactive_attached:
            errmsg = ("Inactive domain XML updated when "
                      "--live option used")

        elif "config" in flag_list and active_attached:
            errmsg = ("Active domain XML updated when "
                      "--config option used")

        # persistent option behavior depends on start_vm
        if "persistent" in flag_list:
            if start_vm:
                if not active_attached or not inactive_attached:
                    errmsg = ("XML not updated when --persistent "
                              "option used on active domain")

            else:
                if not inactive_attached:
                    errmsg = ("XML not updated when --persistent "
                              "option used on inactive domain")
        if len(flag_list) == 0:
            # Not specifying any flag is the same as specifying --current
            if start_vm:
                if not active_attached:
                    errmsg = "Active domain XML not updated"
                elif inactive_attached:
                    errmsg = ("Inactive domain XML updated when active "
                              "requested")

    # Log some debugging info before destroying instances
    if errmsg is not None:
        logging.debug("Active XML:")
        logging.debug(str(active_vmxml))
        logging.debug("Inactive XML:")
        logging.debug(str(inactive_vmxml))
        logging.debug("active_attached: %s", str(active_attached))
        logging.debug("inctive_attached: %s", str(inactive_attached))
        logging.debug("Device XML:")
        logging.debug(open(update_xmlfile, "r").read())

    # clean up tmp files
    del vmxml
    del active_vmxml
    del inactive_vmxml
    os.unlink(update_xmlfile)

    if errmsg is not None:
        raise error.TestFail(errmsg)
