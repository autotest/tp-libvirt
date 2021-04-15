import os
import time
import logging
from virttest import virsh
from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test command: virsh change-media.

    The command changes the media used by CD or floppy drives.

    Test steps:
    1. Prepare test environment.
    2. Perform virsh change-media operation.
    3. Recover test environment.
    4. Confirm the test result.
    """

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

    def check_result(vm_name, disk_source, disk_type, disk_target,
                     flags, vm_state, attach=True):
        """
        Check the test result of attach/detach-device command.
        """
        active_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        active_attached = is_attached(active_vmxml.devices, disk_type,
                                      disk_source, disk_target)
        if vm_state != "transient":
            inactive_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name,
                                                           options="--inactive")
            inactive_attached = is_attached(inactive_vmxml.devices, disk_type,
                                            disk_source, disk_target)

        if flags.count("config") and not flags.count("live"):
            if vm_state != "transient":
                if attach:
                    if not inactive_attached:
                        test.fail("Inactive domain XML not updated"
                                  " when --config options used for"
                                  " attachment")
                    if vm_state != "shutoff":
                        if active_attached:
                            test.fail("Active domain XML updated"
                                      " when --config options used"
                                      " for attachment")
                else:
                    if inactive_attached:
                        test.fail("Inactive domain XML not updated"
                                  " when --config options used for"
                                  " detachment")
                    if vm_state != "shutoff":
                        if not active_attached:
                            test.fail("Active domain XML updated"
                                      " when --config options used"
                                      " for detachment")
        elif flags.count("live") and not flags.count("config"):
            if attach:
                if vm_state in ["paused", "running", "transient"]:
                    if not active_attached:
                        test.fail("Active domain XML not updated"
                                  " when --live options used for"
                                  " attachment")
                if vm_state in ["paused", "running"]:
                    if inactive_attached:
                        test.fail("Inactive domain XML updated"
                                  " when --live options used for"
                                  " attachment")
            else:
                if vm_state in ["paused", "running", "transient"]:
                    if active_attached:
                        test.fail("Active domain XML not updated"
                                  " when --live options used for"
                                  " detachment")
                if vm_state in ["paused", "running"]:
                    if not inactive_attached:
                        test.fail("Inactive domain XML updated"
                                  " when --live options used for"
                                  " detachment")
        elif flags.count("live") and flags.count("config"):
            if attach:
                if vm_state in ["paused", "running"]:
                    if not active_attached:
                        test.fail("Active domain XML not updated"
                                  " when --live --config options"
                                  " used for attachment")
                    if not inactive_attached:
                        test.fail("Inactive domain XML not updated"
                                  " when --live --config options "
                                  "used for attachment")
            else:
                if vm_state in ["paused", "running"]:
                    if active_attached:
                        test.fail("Active domain XML not updated "
                                  "when --live --config options "
                                  "used for detachment")
                    if inactive_attached:
                        test.fail("Inactive domain XML not updated"
                                  " when --live --config options "
                                  "used for detachment")
        elif flags.count("current") or flags == "":
            if attach:
                if vm_state in ["paused", "running", "transient"]:
                    if not active_attached:
                        test.fail("Active domain XML not updated"
                                  " when --current options used "
                                  "for attachment")
                if vm_state in ["paused", "running"]:
                    if inactive_attached:
                        test.fail("Inactive domain XML updated "
                                  "when --current options used "
                                  "for live attachment")
                if vm_state == "shutoff" and not inactive_attached:
                    test.fail("Inactive domain XML not updated "
                              "when --current options used for "
                              "attachment")
            else:
                if vm_state in ["paused", "running", "transient"]:
                    if active_attached:
                        test.fail("Active domain XML not updated"
                                  " when --current options used "
                                  "for detachment")
                if vm_state in ["paused", "running"]:
                    if not inactive_attached:
                        test.fail("Inactive domain XML updated "
                                  "when --current options used "
                                  "for live detachment")
                if vm_state == "shutoff" and inactive_attached:
                    test.fail("Inactive domain XML not updated "
                              "when --current options used for "
                              "detachment")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm_ref = params.get("change_media_vm_ref")
    action = params.get("change_media_action")
    action_twice = params.get("change_media_action_twice", "")
    pre_vm_state = params.get("pre_vm_state")
    options = params.get("change_media_options")
    options_twice = params.get("change_media_options_twice", "")
    device_type = params.get("change_media_device_type", "cdrom")
    target_bus = params.get("change_media_target_bus", "ide")
    target_device = params.get("change_media_target_device", "hdc")
    init_iso_name = params.get("change_media_init_iso")
    old_iso_name = params.get("change_media_old_iso")
    new_iso_name = params.get("change_media_new_iso")
    virsh_dargs = {"debug": True, "ignore_status": True}

    if device_type not in ['cdrom', 'floppy']:
        test.cancel("Got a invalid device type:/n%s"
                    % device_type)

    # Backup for recovery.
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    old_iso = os.path.join(data_dir.get_tmp_dir(), old_iso_name)
    new_iso = os.path.join(data_dir.get_tmp_dir(), new_iso_name)

    if vm_ref == "name":
        vm_ref = vm_name

    if vm.is_alive():
        vm.destroy(gracefully=False)

    try:
        if not init_iso_name:
            init_iso = ""
        else:
            init_iso = os.path.join(data_dir.get_tmp_dir(),
                                    init_iso_name)

        # Prepare test files.
        libvirt.create_local_disk("iso", old_iso)
        libvirt.create_local_disk("iso", new_iso)

        # Check domain's disk device
        disk_blk = vm_xml.VMXML.get_disk_blk(vm_name)
        logging.info("disk_blk %s", disk_blk)
        if target_device not in disk_blk:
            if vm.is_alive():
                virsh.destroy(vm_name)
            logging.info("Adding device")
            libvirt.create_local_disk("iso", init_iso)
            disk_params = {"disk_type": "file", "device_type": device_type,
                           "driver_name": "qemu", "driver_type": "raw",
                           "target_bus": target_bus, "readonly": "yes"}
            libvirt.attach_additional_device(vm_name, target_device,
                                             init_iso, disk_params)

        vmxml_for_test = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        # Turn VM into certain state.
        if pre_vm_state == "running":
            logging.info("Starting %s..." % vm_name)
            if vm.is_dead():
                vm.start()
                vm.wait_for_login().close()
        elif pre_vm_state == "shutoff":
            logging.info("Shuting down %s..." % vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)
        elif pre_vm_state == "paused":
            logging.info("Pausing %s..." % vm_name)
            if vm.is_dead():
                vm.start()
                vm.wait_for_login().close()
            if not vm.pause():
                test.cancel("Cann't pause the domain")
            time.sleep(5)
        elif pre_vm_state == "transient":
            logging.info("Creating %s..." % vm_name)
            vm.undefine()
            if virsh.create(vmxml_for_test.xml, **virsh_dargs).exit_status:
                vmxml_backup.define()
                test.cancel("Cann't create the domain")

        # Libvirt will ignore --source when action is eject
        attach = True
        device_source = old_iso
        if action == "--eject ":
            source = ""
            attach = False
        else:
            source = device_source

        all_options = action + options + " " + source
        ret = virsh.change_media(vm_ref, target_device,
                                 all_options, ignore_status=True, debug=True)
        status_error = False
        if pre_vm_state == "shutoff":
            if options.count("live"):
                status_error = True
        elif pre_vm_state == "transient":
            if options.count("config"):
                status_error = True

        if vm.is_paused():
            vm.resume()
            vm.wait_for_login().close()
            # For paused vm, change_media for eject/update operation
            # should be executed again for it takes effect
            if ret.exit_status:
                if not action.count("insert") and not options.count("force"):
                    ret = virsh.change_media(vm_ref, target_device, all_options,
                                             ignore_status=True, debug=True)
        if not status_error and ret.exit_status:
            test.fail("Change media failed: %s" % ret.stderr.strip())
        libvirt.check_exit_status(ret, status_error)
        if not ret.exit_status:
            check_result(vm_name, device_source, device_type, target_device,
                         options, pre_vm_state, attach)

        if action_twice:
            if pre_vm_state == "paused":
                if not vm.pause():
                    test.fail("Cann't pause the domain")
                time.sleep(5)
            attach = True
            device_source = new_iso
            if action_twice == "--eject ":
                #options_twice += " --force "
                source = ""
                attach = False
            else:
                source = device_source
            all_options = action_twice + options_twice + " " + source
            time.sleep(5)
            if options_twice == "--config" or pre_vm_state == "shutoff":
                wait_for_event = False
            else:
                wait_for_event = True
            ret = virsh.change_media(vm_ref, target_device, all_options,
                                     wait_for_event=wait_for_event,
                                     ignore_status=True, debug=True)
            status_error = False
            if pre_vm_state == "shutoff":
                if options_twice.count("live"):
                    status_error = True
            elif pre_vm_state == "transient":
                if options_twice.count("config"):
                    status_error = True

            if action_twice == "--insert ":
                if pre_vm_state in ["running", "paused"]:
                    if options in ["--force", "--current", "", "--live"]:
                        if options_twice.count("config"):
                            status_error = True
                    elif options == "--config":
                        if options_twice in ["--force", "--current", ""]:
                            status_error = True
                        elif options_twice in ["--config --live"] and pre_vm_state in ["running"]:
                            status_error = False
                        elif options_twice.count("live"):
                            status_error = True
                elif pre_vm_state == "transient":
                    if ret.exit_status:
                        status_error = True
                elif pre_vm_state == "shutoff":
                    if options.count("live"):
                        status_error = True
            if vm.is_paused():
                vm.resume()
                vm.wait_for_login().close()
                # For paused vm, change_media for eject/update operation
                # should be executed again for it takes effect
                if ret.exit_status and not action_twice.count("insert"):
                    ret = virsh.change_media(vm_ref, target_device, all_options,
                                             wait_for_event=wait_for_event,
                                             ignore_status=True, debug=True)
            if not status_error and ret.exit_status:
                test.fail("Change media failed: %s" % ret.stderr.strip())
            libvirt.check_exit_status(ret, status_error)
            if not ret.exit_status:
                check_result(vm_name, device_source, device_type, target_device,
                             options_twice, pre_vm_state, attach)

        # Try to start vm.
        if vm.is_dead():
            vm.start()
            vm.wait_for_login().close()
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Recover xml of vm.
        vmxml_backup.sync()
        # Remove disks
        if os.path.exists(init_iso):
            os.remove(init_iso)
        if os.path.exists(old_iso):
            os.remove(old_iso)
        if os.path.exists(init_iso):
            os.remove(new_iso)
