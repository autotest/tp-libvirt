import os
import time
import shutil
import logging

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import libvirt_version

from virttest.libvirt_xml import VMXML
from virttest.utils_test import libvirt


def create_disk(vm_name, disk_iso, disk_type, target_dev, mode=""):
    """
    :param vm_name : vm_name
    :param disk_iso : disk's source backing file
    :param disk_type: disk's device type: cdrom or floppy
    :param target_dev: disk's target device name
    :param mode: readonly or shareable
    """
    libvirt.create_local_disk("iso", disk_iso)
    options = "--type %s --sourcetype=file --config" % disk_type
    if mode:
        options += " --mode %s" % mode
    try:
        virsh.attach_disk(vm_name, disk_iso, target_dev, options)
    except Exception:
        os.remove(disk_iso)
        raise exceptions.TestFail("Failed to attach")


def create_attach_xml(update_xmlfile, disk_type, target_bus,
                      target_dev, source_iso="", disk_mode="",
                      disk_alias=None):
    """
    Create a xml file to update a device.

    :param update_xmlfile : path/file to save device XML
    :param source_iso : disk's source backing file.
    :param disk_type: disk's device type: cdrom or floppy
    :param target_bus: disk's target bus
    :param target_dev: disk's target device name
    :param disk_mode: readonly or shareable
    :param disk_alias: disk's alias name
    """
    if source_iso:
        libvirt.create_local_disk("iso", source_iso)
    disk_class = VMXML.get_device_class('disk')
    disk = disk_class(type_name='file')
    # Static definition for comparison in check_attach()
    disk.device = disk_type
    disk.target = dict(bus=target_bus, dev=target_dev)
    if source_iso:
        disk.driver = dict(name='qemu')
        disk.source = disk.new_disk_source(attrs={'file': source_iso})
        if disk_mode == "readonly":
            disk.readonly = True
        if disk_mode == "shareable":
            disk.share = True
    if disk_alias:
        disk.alias = dict(name=disk_alias)
    disk.xmltreefile.write()
    shutil.copyfile(disk.xml, update_xmlfile)
    logging.debug('Disk xml created: \n %s', disk)


def run(test, params, env):
    """
    Test command: virsh update-device.

    Update device from an XML <file>.
    1.Prepare test environment, adding a cdrom/floppy to VM.
    2.Perform virsh update-device operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pre_vm_state = params.get("at_dt_device_pre_vm_state")
    virsh_dargs = {"debug": True, "ignore_status": True}

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
            logging.debug("Check disk XML:\n%s", open(disk['xml']).read())
            if disk.device != disk_type:
                continue
            if disk.target['dev'] != target_dev:
                continue
            if disk.xmltreefile.find('source') is not None and \
                    'file' in disk.source.attrs:
                if disk.source.attrs['file'] != source_file:
                    continue
            else:
                continue
            # All three conditions met
            logging.debug("Find %s in given disk XML", source_file)
            return True
        logging.debug("Not find %s in gievn disk XML", source_file)
        return False

    def check_result(disk_source, disk_type, disk_target,
                     flags, attach=True):
        """
        Check the test result of update-device command.
        """
        vm_state = pre_vm_state
        active_vmxml = VMXML.new_from_dumpxml(vm_name)
        active_attached = is_attached(active_vmxml.devices, disk_type,
                                      disk_source, disk_target)
        if vm_state != "transient":
            inactive_vmxml = VMXML.new_from_dumpxml(vm_name,
                                                    options="--inactive")
            inactive_attached = is_attached(inactive_vmxml.devices, disk_type,
                                            disk_source, disk_target)

        if flags.count("config") and not flags.count("live"):
            if vm_state != "transient":
                if attach:
                    if not inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML not updated"
                                                  " when --config options used for"
                                                  " attachment")
                    if vm_state != "shutoff":
                        if active_attached:
                            raise exceptions.TestFail("Active domain XML updated "
                                                      "when --config options used"
                                                      " for attachment")
                else:
                    if inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML not updated"
                                                  " when --config options used for"
                                                  " detachment")
                    if vm_state != "shutoff":
                        if not active_attached:
                            raise exceptions.TestFail("Active domain XML updated "
                                                      "when --config options used"
                                                      " for detachment")
        elif flags.count("live") and not flags.count("config"):
            if attach:
                if vm_state in ["paused", "running", "transient"]:
                    if not active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --live options used for"
                                                  " attachment")
                if vm_state in ["paused", "running"]:
                    if inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML updated "
                                                  "when --live options used for"
                                                  " attachment")
            else:
                if vm_state in ["paused", "running", "transient"]:
                    if active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --live options used for"
                                                  " detachment")
                if vm_state in ["paused", "running"]:
                    if not inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML updated "
                                                  "when --live options used for"
                                                  " detachment")
        elif flags.count("live") and flags.count("config"):
            if attach:
                if vm_state in ["paused", "running"]:
                    if not active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --live --config options"
                                                  " used for attachment")
                    if not inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML not updated"
                                                  " when --live --config options "
                                                  "used for attachment")
            else:
                if vm_state in ["paused", "running"]:
                    if active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --live --config options"
                                                  " used for detachment")
                    if inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML not updated"
                                                  " when --live --config options "
                                                  "used for detachment")
        elif flags.count("current") or flags == "":
            if attach:
                if vm_state in ["paused", "running", "transient"]:
                    if not active_attached:
                        raise exceptions.TestFail("Active domain XML not updated "
                                                  "when --current options used "
                                                  "for attachment")
                if vm_state in ["paused", "running"]:
                    if inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML updated "
                                                  "when --current options used "
                                                  "for live attachment")
                if vm_state == "shutoff" and not inactive_attached:
                    raise exceptions.TestFail("Inactive domain XML not updated "
                                              "when --current options used for "
                                              "attachment")
            else:
                if vm_state in ["paused", "running", "transient"]:
                    if active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --current options used "
                                                  "for detachment")
                if vm_state in ["paused", "running"]:
                    if not inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML updated "
                                                  "when --current options used "
                                                  "for live detachment")
                if vm_state == "shutoff" and inactive_attached:
                    raise exceptions.TestFail("Inactive domain XML not updated"
                                              " when --current options used "
                                              "for detachment")

    def check_rhel_version(release_ver, session=None):
        """
        Login to guest and check its release version
        """
        rhel_release = {"rhel6": "Red Hat Enterprise Linux Server release 6",
                        "rhel7": "Red Hat Enterprise Linux Server release 7",
                        "fedora": "Fedora release"}
        version_file = "/etc/redhat-release"
        if release_ver not in rhel_release:
            logging.error("Can't support this version of guest: %s",
                          release_ver)
            return False

        cmd = "grep '%s' %s" % (rhel_release[release_ver], version_file)
        if session:
            s = session.cmd_status(cmd)
        else:
            s = process.run(cmd, ignore_status=True, shell=True).exit_status

        logging.debug("Check version cmd return:%s", s)
        if s == 0:
            return True
        else:
            return False

    vmxml_backup = VMXML.new_from_dumpxml(vm_name, options="--inactive")
    # Before doing anything - let's be sure we can support this test
    # Parse flag list, skip testing early if flag is not supported
    # NOTE: "".split("--") returns [''] which messes up later empty test
    at_flag = params.get("at_dt_device_at_options", "")
    dt_flag = params.get("at_dt_device_dt_options", "")
    flag_list = []
    if at_flag.count("--"):
        flag_list.extend(at_flag.split("--"))
    if dt_flag.count("--"):
        flag_list.extend(dt_flag.split("--"))
    for item in flag_list:
        option = item.strip()
        if option == "":
            continue
        if not bool(virsh.has_command_help_match("update-device", option)):
            raise exceptions.TestSkipError("virsh update-device doesn't support "
                                           "--%s" % option)

    # As per RH BZ 961443 avoid testing before behavior changes
    if 'config' in flag_list:
        # SKIP tests using --config if libvirt is 0.9.10 or earlier
        if not libvirt_version.version_compare(0, 9, 10):
            raise exceptions.TestSkipError("BZ 961443: --config behavior change "
                                           "in version 0.9.10")
    if 'persistent' in flag_list or 'live' in flag_list:
        # SKIP tests using --persistent if libvirt 1.0.5 or earlier
        if not libvirt_version.version_compare(1, 0, 5):
            raise exceptions.TestSkipError("BZ 961443: --persistent behavior "
                                           "change in version 1.0.5")

    # Get the target bus/dev
    disk_type = params.get("disk_type", "cdrom")
    target_bus = params.get("updatedevice_target_bus", "ide")
    target_dev = params.get("updatedevice_target_dev", "hdc")
    disk_mode = params.get("disk_mode", "")
    support_mode = ['readonly', 'shareable']
    if not disk_mode and disk_mode not in support_mode:
        raise exceptions.TestError("%s not in support mode %s"
                                   % (disk_mode, support_mode))

    # Prepare tmp directory and files.
    orig_iso = os.path.join(data_dir.get_tmp_dir(), "orig.iso")
    test_iso = os.path.join(data_dir.get_tmp_dir(), "test.iso")

    # Check the version first.
    host_rhel6 = False
    guest_rhel6 = False
    if not params.get("skip_release_check", "no") == "yes":
        host_rhel6 = check_rhel_version('rhel6')
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        if check_rhel_version('rhel6', session):
            guest_rhel6 = True
        session.close()
    vm.destroy(gracefully=False)

    try:
        # Prepare the disk first.
        create_disk(vm_name, orig_iso, disk_type, target_dev, disk_mode)
        vmxml_for_test = VMXML.new_from_dumpxml(vm_name,
                                                options="--inactive")

        # Turn VM into certain state.
        if pre_vm_state == "running":
            if at_flag == "--config" or dt_flag == "--config":
                if host_rhel6:
                    raise exceptions.TestSkipError("Config option not supported"
                                                   " on this host")
            logging.info("Starting %s..." % vm_name)
            if vm.is_dead():
                vm.start()
                vm.wait_for_login().close()
        elif pre_vm_state == "shutoff":
            if not at_flag or not dt_flag:
                if host_rhel6:
                    raise exceptions.TestSkipError("Default option not supported"
                                                   " on this host")
            logging.info("Shuting down %s..." % vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)
        elif pre_vm_state == "paused":
            if at_flag == "--config" or dt_flag == "--config":
                if host_rhel6:
                    raise exceptions.TestSkipError("Config option not supported"
                                                   " on this host")
            logging.info("Pausing %s..." % vm_name)
            if vm.is_dead():
                vm.start()
                vm.wait_for_login().close()
            if not vm.pause():
                raise exceptions.TestSkipError("Cann't pause the domain")
        elif pre_vm_state == "transient":
            logging.info("Creating %s..." % vm_name)
            vm.undefine()
            if virsh.create(vmxml_for_test.xml, **virsh_dargs).exit_status:
                vmxml_backup.define()
                raise exceptions.TestSkipError("Cann't create the domain")
            vm.wait_for_login().close()
    except Exception as e:
        logging.error(str(e))
        if os.path.exists(orig_iso):
            os.remove(orig_iso)
        vmxml_backup.sync()
        raise exceptions.TestSkipError(str(e))

    # Get remaining parameters for configuration.
    vm_ref = params.get("updatedevice_vm_ref", "domname")
    at_status_error = "yes" == params.get("at_status_error", "no")
    dt_status_error = "yes" == params.get("dt_status_error", "no")

    dom_uuid = vm.get_uuid()
    dom_id = vm.get_id()
    # Set domain reference.
    if vm_ref == "domname":
        vm_ref = vm_name
    elif vm_ref == "domid":
        vm_ref = dom_id
    elif vm_ref == "domuuid":
        vm_ref = dom_uuid
    elif vm_ref == "hexdomid" and dom_id is not None:
        vm_ref = hex(int(dom_id))

    try:
        # Get disk alias
        disk_alias = libvirt.get_disk_alias(vm, orig_iso)

        # Firstly detach the disk.
        update_xmlfile = os.path.join(data_dir.get_tmp_dir(),
                                      "update.xml")
        create_attach_xml(update_xmlfile, disk_type, target_bus,
                          target_dev, "", disk_mode, disk_alias)
        ret = virsh.update_device(vm_ref, filearg=update_xmlfile,
                                  flagstr=dt_flag, ignore_status=True,
                                  debug=True)
        if vm.is_paused():
            vm.resume()
            vm.wait_for_login().close()
        if vm.is_alive() and not guest_rhel6:
            time.sleep(5)
            # For rhel7 guest, need to update twice for it to take effect.
            ret = virsh.update_device(vm_ref, filearg=update_xmlfile,
                                      flagstr=dt_flag, ignore_status=True,
                                      debug=True)
        os.remove(update_xmlfile)
        libvirt.check_exit_status(ret, dt_status_error)
        if not ret.exit_status:
            check_result(orig_iso, disk_type, target_dev, dt_flag, False)

        # Then attach the disk.
        if pre_vm_state == "paused":
            if not vm.pause():
                raise exceptions.TestFail("Cann't pause the domain")
        create_attach_xml(update_xmlfile, disk_type, target_bus,
                          target_dev, test_iso, disk_mode, disk_alias)
        ret = virsh.update_device(vm_ref, filearg=update_xmlfile,
                                  flagstr=at_flag, ignore_status=True,
                                  debug=True)
        if vm.is_paused():
            vm.resume()
            vm.wait_for_login().close()
        update_twice = False
        if vm.is_alive() and not guest_rhel6:
            # For rhel7 guest, need to update twice for it to take effect.
            if (pre_vm_state in ["running", "paused"] and
                    dt_flag == "--config" and at_flag != "--config"):
                update_twice = True
            elif (pre_vm_state == "transient" and
                    dt_flag.count("config") and not at_flag.count("config")):
                update_twice = True
        if update_twice:
            time.sleep(5)
            ret = virsh.update_device(vm_ref, filearg=update_xmlfile,
                                      flagstr=at_flag, ignore_status=True,
                                      debug=True)
        libvirt.check_exit_status(ret, at_status_error)
        os.remove(update_xmlfile)
        if not ret.exit_status:
            check_result(test_iso, disk_type, target_dev, at_flag)
        # Try to start vm at last.
        if vm.is_dead():
            vm.start()
            vm.wait_for_login().close()

    finally:
        vm.destroy(gracefully=False, free_mac_addresses=False)
        vmxml_backup.sync()
        if os.path.exists(orig_iso):
            os.remove(orig_iso)
        if os.path.exists(test_iso):
            os.remove(test_iso)
