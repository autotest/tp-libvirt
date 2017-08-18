import os
import time
import logging
from avocado.core import exceptions
from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test virsh {at|de}tach-disk command.

    The command can attach new disk/detach disk.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh attach/detach-disk operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    pre_vm_state = params.get("at_dt_disk_pre_vm_state", "running")
    virsh_dargs = {'debug': True, 'ignore_status': True}

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

    def check_result(disk_source, disk_type, disk_target,
                     flags, attach=True):
        """
        Check the test result of attach/detach-device command.
        """
        active_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        if not attach:
            utils_misc.wait_for(lambda: not is_attached(active_vmxml.devices,
                                                        disk_type, disk_source, disk_target), 20)
        active_attached = is_attached(active_vmxml.devices, disk_type,
                                      disk_source, disk_target)
        vm_state = pre_vm_state
        if vm_state != "transient":
            inactive_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name,
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
                                                      "when --config options used "
                                                      "for attachment")
                else:
                    if inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML not updated"
                                                  " when --config options used for"
                                                  " detachment")
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
                        raise exceptions.TestFail("Active domain XML not updated "
                                                  "when --live --config options "
                                                  "used for detachment")
                    if inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML updated "
                                                  "when --live --config options"
                                                  " used for detachment")
        elif flags.count("current") or flags == "":
            if attach:
                if vm_state in ["paused", "running", "transient"]:
                    if not active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --current options used "
                                                  "for attachment")
                if vm_state in ["paused", "running"]:
                    if inactive_attached:
                        raise exceptions.TestFail("Inactive domain XML updated "
                                                  "when --current options used "
                                                  "for live attachment")
                if vm_state == "shutoff" and not inactive_attached:
                    raise exceptions.TestFail("Inactive domain XML not updated"
                                              " when --current options used for"
                                              " attachment")
            else:
                if vm_state in ["paused", "running", "transient"]:
                    if active_attached:
                        raise exceptions.TestFail("Active domain XML not updated"
                                                  " when --current options used "
                                                  "for detachment")
                if vm_state == "shutoff" and inactive_attached:
                    raise exceptions.TestFail("Inactive domain XML not updated"
                                              " when --current options used for"
                                              " detachment")

    vm_ref = params.get("at_dt_disk_vm_ref", "name")
    at_status_error = "yes" == params.get("at_status_error", 'no')
    dt_status_error = "yes" == params.get("dt_status_error", 'no')
    # Disk specific attributes.
    at_options = params.get("at_dt_disk_at_options", "")
    dt_options = params.get("at_dt_disk_dt_options", "")
    device = params.get("at_dt_disk_device", "disk")
    device_source_name = params.get("at_dt_disk_device_source",
                                    "attach.img")
    device_target = params.get("at_dt_disk_device_target", "vdd")

    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Turn VM into certain state.
    if pre_vm_state == "running":
        logging.info("Starting %s...", vm_name)
        if vm.is_dead():
            vm.start()
            vm.wait_for_login().close()
    elif pre_vm_state == "shutoff":
        logging.info("Shuting down %s...", vm_name)
        if vm.is_alive():
            vm.destroy(gracefully=False)
    elif pre_vm_state == "paused":
        logging.info("Pausing %s...", vm_name)
        if vm.is_dead():
            vm.start()
            vm.wait_for_login().close()
        if not vm.pause():
            raise exceptions.TestSkipError("Cann't pause the domain")
    elif pre_vm_state == "transient":
        logging.info("Creating %s...", vm_name)
        vm.undefine()
        if virsh.create(backup_xml.xml, **virsh_dargs).exit_status:
            backup_xml.define()
            raise exceptions.TestSkipError("Cann't create the domain")
        vm.wait_for_login().close()

    # Test.
    domid = vm.get_id()
    domuuid = vm.get_uuid()

    # Confirm how to reference a VM.
    if vm_ref == "name":
        vm_ref = vm_name
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref == "uuid":
        vm_ref = domuuid
    else:
        vm_ref = ""

    try:
        # Create disk image.
        device_source = os.path.join(data_dir.get_tmp_dir(),
                                     device_source_name)
        libvirt.create_local_disk("file", device_source, "1")

        # Attach the disk.
        ret = virsh.attach_disk(vm_ref, device_source, device_target,
                                at_options, debug=True)
        libvirt.check_exit_status(ret, at_status_error)

        # Check if command take affect in config file.
        if vm.is_paused():
            vm.resume()
            vm.wait_for_login().close()

        #Sleep a while for vm is stable
        time.sleep(3)
        if not ret.exit_status:
            check_result(device_source, device, device_target,
                         at_options)

        # Detach the disk.
        if pre_vm_state == "paused":
            if not vm.pause():
                raise exceptions.TestFail("Cann't pause the domain")
        ret = virsh.detach_disk(vm_ref, device_target, dt_options,
                                debug=True)
        libvirt.check_exit_status(ret, dt_status_error)

        # Check if command take affect config file.
        if vm.is_paused():
            vm.resume()
            vm.wait_for_login().close()

        #Sleep a while for vm is stable
        time.sleep(10)
        if not ret.exit_status:
            check_result(device_source, device, device_target,
                         dt_options, False)

        # Try to start vm at last.
        if vm.is_dead():
            vm.start()
            vm.wait_for_login().close()

    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()

        if os.path.exists(device_source):
            os.remove(device_source)
