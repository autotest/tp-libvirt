import os
import time
import logging

import aexpect

from autotest.client.shared import error
from autotest.client.shared import utils

from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.staging.service import Factory
from virttest.staging import lv_utils
from virttest import utils_misc

from provider import libvirt_version


def run(test, params, env):
    """
    Test virsh {at|de}tach-disk command.

    The command can attach new disk/detach disk.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh attach/detach-disk operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    def check_vm_partition(vm, device, os_type, target_name, old_parts):
        """
        Check VM disk's partition.

        :param vm. VM guest.
        :param os_type. VM's operation system type.
        :param target_name. Device target type.
        :return: True if check successfully.
        """
        logging.info("Checking VM partittion...")
        if vm.is_dead():
            vm.start()
        try:
            attached = False
            if os_type == "linux":
                session = vm.wait_for_login()
                new_parts = libvirt.get_parts_list(session)
                added_parts = list(set(new_parts).difference(set(old_parts)))
                logging.debug("Added parts: %s" % added_parts)
                for i in range(len(added_parts)):
                    if device == "disk":
                        if target_name.startswith("vd"):
                            if added_parts[i].startswith("vd"):
                                attached = True
                        elif target_name.startswith("hd") or target_name.startswith("sd"):
                            if added_parts[i].startswith("sd"):
                                attached = True
                    elif device == "cdrom":
                        if added_parts[i].startswith("sr"):
                            attached = True
                session.close()
            return attached
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    def acpiphp_module_modprobe(vm, os_type):
        """
        Add acpiphp module if VM's os type is rhle5.*

        :param vm. VM guest.
        :param os_type. VM's operation system type.
        :return: True if operate successfully.
        """
        if vm.is_dead():
            vm.start()
        try:
            if os_type == "linux":
                session = vm.wait_for_login()
                s_rpm, _ = session.cmd_status_output(
                    "rpm --version")
                # If status is different from 0, this
                # guest OS doesn't support the rpm package
                # manager
                if s_rpm:
                    session.close()
                    return True
                _, o_vd = session.cmd_status_output(
                    "rpm -qa | grep redhat-release")
                if o_vd.find("5Server") != -1:
                    s_mod, o_mod = session.cmd_status_output(
                        "modprobe acpiphp")
                    del o_mod
                    if s_mod != 0:
                        session.close()
                        return False
                session.close()
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    # Get test command.
    test_cmd = params.get("at_dt_disk_test_cmd", "attach-disk")

    vm_ref = params.get("at_dt_disk_vm_ref", "name")
    at_options = params.get("at_dt_disk_at_options", "")
    dt_options = params.get("at_dt_disk_dt_options", "")
    at_with_shareable = "yes" == params.get("at_with_shareable", 'no')
    pre_vm_state = params.get("at_dt_disk_pre_vm_state", "running")
    status_error = "yes" == params.get("status_error", 'no')
    no_attach = params.get("at_dt_disk_no_attach", 'no')
    os_type = params.get("os_type", "linux")
    qemu_file_lock = params.get("qemu_file_lock", "")
    if qemu_file_lock:
        if utils_misc.compare_qemu_version(2, 9, 0):
            logging.info('From qemu-kvm-rhev 2.9.0:'
                         'QEMU image locking, which should prevent multiple '
                         'runs of QEMU or qemu-img when a VM is running.')
            if test_cmd == "detach-disk" or pre_vm_state == "shut off":
                test.cancel('This case is not supported.')
            else:
                logging.info('The expect result is failure as opposed with succeed')
                status_error = True

    # Disk specific attributes.
    device = params.get("at_dt_disk_device", "disk")
    device_source_name = params.get("at_dt_disk_device_source", "attach.img")
    device_source_format = params.get("at_dt_disk_device_source_format", "raw")
    device_target = params.get("at_dt_disk_device_target", "vdd")
    device_disk_bus = params.get("at_dt_disk_bus_type", "virtio")
    source_path = "yes" == params.get("at_dt_disk_device_source_path", "yes")
    create_img = "yes" == params.get("at_dt_disk_create_image", "yes")
    test_twice = "yes" == params.get("at_dt_disk_test_twice", "no")
    test_type = "yes" == params.get("at_dt_disk_check_type", "no")
    test_audit = "yes" == params.get("at_dt_disk_check_audit", "no")
    test_block_dev = "yes" == params.get("at_dt_disk_iscsi_device", "no")
    test_logcial_dev = "yes" == params.get("at_dt_disk_logical_device", "no")
    restart_libvirtd = "yes" == params.get("at_dt_disk_restart_libvirtd", "no")
    vg_name = params.get("at_dt_disk_vg", "vg_test_0")
    lv_name = params.get("at_dt_disk_lv", "lv_test_0")
    serial = params.get("at_dt_disk_serial", "")
    address = params.get("at_dt_disk_address", "")
    address2 = params.get("at_dt_disk_address2", "")
    cache_options = params.get("cache_options", "")
    time_sleep = params.get("time_sleep", 3)
    if at_with_shareable:
        at_options += " --mode shareable"
    if serial:
        at_options += (" --serial %s" % serial)
    if address2:
        at_options_twice = at_options + (" --address %s" % address2)
    if address:
        at_options += (" --address %s" % address)
    if cache_options:
        if cache_options.count("directsync"):
            if not libvirt_version.version_compare(1, 0, 0):
                test.cancel("'directsync' cache option doesn't "
                            "support in current libvirt version.")
        at_options += (" --cache %s" % cache_options)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Start vm and get all partions in vm.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = libvirt.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Create virtual device file.
    device_source_path = os.path.join(test.tmpdir, device_source_name)
    if test_block_dev:
        device_source = libvirt.setup_or_cleanup_iscsi(True)
        if not device_source:
            # We should skip this case
            raise error.TestNAError("Can not get iscsi device name in host")
        if test_logcial_dev:
            lv_utils.vg_create(vg_name, device_source)
            device_source = libvirt.create_local_disk("lvm",
                                                      size="10M",
                                                      vgname=vg_name,
                                                      lvname=lv_name)
            logging.debug("New created volume: %s", lv_name)
    else:
        if source_path and create_img:
            device_source = libvirt.create_local_disk(
                "file", path=device_source_path,
                size="1G", disk_format=device_source_format)
        else:
            device_source = device_source_name

    # if we are testing audit, we need to start audit servcie first.
    if test_audit:
        auditd_service = Factory.create_service("auditd")
        if not auditd_service.status():
            auditd_service.start()
        logging.info("Auditd service status: %s" % auditd_service.status())

    # If we are testing cdrom device, we need to detach hdc in VM first.
    if device == "cdrom":
        if vm.is_alive():
            vm.destroy(gracefully=False)
        s_detach = virsh.detach_disk(vm_name, device_target, "--config")
        if not s_detach:
            logging.error("Detach hdc failed before test.")

    # If we are testing detach-disk, we need to attach certain device first.
    if test_cmd == "detach-disk" and no_attach != "yes":
        s_at_options = "--driver qemu --config"
        if at_with_shareable:
            s_at_options += ' --mode shareable'
        s_attach = virsh.attach_disk(vm_name, device_source, device_target,
                                     s_at_options).exit_status
        if s_attach != 0:
            logging.error("Attaching device failed before testing detach-disk")

        if test_twice:
            device_target2 = params.get("at_dt_disk_device_target2",
                                        device_target)
            device_source = libvirt.create_local_disk(
                "file", path=device_source_path,
                size="1", disk_format=device_source_format)
            s_attach = virsh.attach_disk(vm_name, device_source, device_target2,
                                         s_at_options).exit_status
            if s_attach != 0:
                logging.error("Attaching device failed before testing "
                              "detach-disk test_twice")

    vm.start()
    vm.wait_for_login()

    # Add acpiphp module before testing if VM's os type is rhle5.*
    if not acpiphp_module_modprobe(vm, os_type):
        raise error.TestError("Add acpiphp module failed before test.")

    # Turn VM into certain state.
    if pre_vm_state == "paused":
        logging.info("Suspending %s..." % vm_name)
        if vm.is_alive():
            vm.pause()
    elif pre_vm_state == "shut off":
        logging.info("Shuting down %s..." % vm_name)
        if vm.is_alive():
            vm.destroy(gracefully=False)

    # Get disk count before test.
    disk_count_before_cmd = vm_xml.VMXML.get_disk_count(vm_name)

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

    if test_cmd == "attach-disk":
        status = virsh.attach_disk(vm_ref, device_source, device_target,
                                   at_options, debug=True).exit_status
    elif test_cmd == "detach-disk":
        status = virsh.detach_disk(vm_ref, device_target, dt_options,
                                   debug=True).exit_status

    if restart_libvirtd:
        libvirtd_serv = utils_libvirtd.Libvirtd()
        libvirtd_serv.restart()

    if test_twice:
        device_target2 = params.get("at_dt_disk_device_target2", device_target)
        device_source = libvirt.create_local_disk(
            "file", path=device_source_path,
            size="1G", disk_format=device_source_format)
        if test_cmd == "attach-disk":
            if address2:
                at_options = at_options_twice
            status = virsh.attach_disk(vm_ref, device_source,
                                       device_target2, at_options,
                                       debug=True).exit_status
        elif test_cmd == "detach-disk":
            status = virsh.detach_disk(vm_ref, device_target2, dt_options,
                                       debug=True).exit_status

    # Resume guest after command. On newer libvirt this is fixed as it has
    # been a bug. The change in xml file is done after the guest is resumed.
    if pre_vm_state == "paused":
        vm.resume()

    # Check audit log
    check_audit_after_cmd = True
    if test_audit:
        grep_audit = ('grep "%s" /var/log/audit/audit.log'
                      % test_cmd.split("-")[0])
        cmd = (grep_audit + ' | ' + 'grep "%s" | tail -n1 | grep "res=success"'
               % device_source)
        if utils.run(cmd).exit_status:
            logging.error("Audit check failed")
            check_audit_after_cmd = False

    # Need wait a while for xml to sync
    time.sleep(float(time_sleep))
    # Check disk count after command.
    check_count_after_cmd = True
    disk_count_after_cmd = vm_xml.VMXML.get_disk_count(vm_name)
    if test_cmd == "attach-disk":
        if disk_count_after_cmd == disk_count_before_cmd:
            check_count_after_cmd = False
    elif test_cmd == "detach-disk":
        if disk_count_after_cmd < disk_count_before_cmd:
            check_count_after_cmd = False

    # Recover VM state.
    if pre_vm_state == "shut off":
        vm.start()

    # Check in VM after command.
    check_vm_after_cmd = True
    check_vm_after_cmd = check_vm_partition(vm, device, os_type,
                                            device_target, old_parts)

    # Check disk type after attach.
    check_disk_type = True
    if test_type:
        if test_block_dev:
            check_disk_type = vm_xml.VMXML.check_disk_type(vm_name,
                                                           device_source,
                                                           "block")
        else:
            check_disk_type = vm_xml.VMXML.check_disk_type(vm_name,
                                                           device_source,
                                                           "file")
    # Check disk serial after attach.
    check_disk_serial = True
    if serial:
        disk_serial = vm_xml.VMXML.get_disk_serial(vm_name, device_target)
        if serial != disk_serial:
            check_disk_serial = False

    # Check disk address after attach.
    check_disk_address = True
    if address:
        disk_address = vm_xml.VMXML.get_disk_address(vm_name, device_target)
        if address != disk_address:
            check_disk_address = False

    # Check multifunction address after attach.
    check_disk_address2 = True
    if address2:
        disk_address2 = vm_xml.VMXML.get_disk_address(vm_name, device_target2)
        if address2 != disk_address2:
            check_disk_address2 = False

    # Check disk cache option after attach.
    check_cache_after_cmd = True
    if cache_options:
        disk_cache = vm_xml.VMXML.get_disk_attr(vm_name, device_target,
                                                "driver", "cache")
        if cache_options == "default":
            if disk_cache is not None:
                check_cache_after_cmd = False
        elif disk_cache != cache_options:
            check_cache_after_cmd = False

    # Eject cdrom test
    eject_cdrom = "yes" == params.get("at_dt_disk_eject_cdrom", "no")
    save_vm = "yes" == params.get("at_dt_disk_save_vm", "no")
    save_file = os.path.join(test.tmpdir, "vm.save")
    try:
        if eject_cdrom:
            eject_params = {'type_name': "file", 'device_type': "cdrom",
                            'target_dev': device_target, 'target_bus': device_disk_bus}
            eject_xml = libvirt.create_disk_xml(eject_params)
            logging.debug("Eject CDROM by XML: %s", open(eject_xml).read())
            # Run command tiwce to make sure cdrom tray open first #BZ892289
            # Open tray
            virsh.attach_device(domainarg=vm_name, filearg=eject_xml, debug=True)
            # Add time sleep between two attach commands.
            if time_sleep:
                time.sleep(float(time_sleep))
            # Eject cdrom
            result = virsh.attach_device(domainarg=vm_name, filearg=eject_xml,
                                         debug=True)
            if result.exit_status != 0:
                raise error.TestFail("Eject CDROM failed")
            if vm_xml.VMXML.check_disk_exist(vm_name, device_source):
                raise error.TestFail("Find %s after do eject" % device_source)
        # Save and restore VM
        if save_vm:
            result = virsh.save(vm_name, save_file, debug=True)
            libvirt.check_exit_status(result)
            result = virsh.restore(save_file, debug=True)
            libvirt.check_exit_status(result)
            if vm_xml.VMXML.check_disk_exist(vm_name, device_source):
                raise error.TestFail("Find %s after do restore" % device_source)

        # Destroy VM.
        vm.destroy(gracefully=False)

        # Check disk count after VM shutdown (with --config).
        check_count_after_shutdown = True
        inactive_vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        disk_count_after_shutdown = len(inactive_vmxml.get_disk_all())
        if test_cmd == "attach-disk":
            if disk_count_after_shutdown == disk_count_before_cmd:
                check_count_after_shutdown = False
        elif test_cmd == "detach-disk":
            if disk_count_after_shutdown < disk_count_before_cmd:
                check_count_after_shutdown = False

    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
        if os.path.exists(save_file):
            os.remove(save_file)
        if test_block_dev:
            if test_logcial_dev:
                libvirt.delete_local_disk("lvm", vgname=vg_name, lvname=lv_name)
                lv_utils.vg_remove(vg_name)
                utils.system("pvremove %s" % device_source, ignore_status=True)
            libvirt.setup_or_cleanup_iscsi(False)
        else:
            libvirt.delete_local_disk("file", device_source)

    # Check results.
    if status_error:
        if not status:
            raise error.TestFail("virsh %s exit with unexpected value."
                                 % test_cmd)
    else:
        if status:
            raise error.TestFail("virsh %s failed." % test_cmd)
        if test_cmd == "attach-disk":
            if at_options.count("config"):
                if not check_count_after_shutdown:
                    raise error.TestFail("Cannot see config attached device "
                                         "in xml file after VM shutdown.")
                if not check_disk_serial:
                    raise error.TestFail("Serial set failed after attach")
                if not check_disk_address:
                    raise error.TestFail("Address set failed after attach")
                if not check_disk_address2:
                    raise error.TestFail("Address(multifunction) set failed"
                                         " after attach")
            else:
                if not check_count_after_cmd:
                    raise error.TestFail("Cannot see device in xml file"
                                         " after attach.")
                if not check_vm_after_cmd:
                    raise error.TestFail("Cannot see device in VM after"
                                         " attach.")
                if not check_disk_type:
                    raise error.TestFail("Check disk type failed after"
                                         " attach.")
                if not check_audit_after_cmd:
                    raise error.TestFail("Audit hotplug failure after attach")
                if not check_cache_after_cmd:
                    raise error.TestFail("Check cache failure after attach")
                if at_options.count("persistent"):
                    if not check_count_after_shutdown:
                        raise error.TestFail("Cannot see device attached "
                                             "with persistent after "
                                             "VM shutdown.")
                else:
                    if check_count_after_shutdown:
                        raise error.TestFail("See non-config attached device "
                                             "in xml file after VM shutdown.")
        elif test_cmd == "detach-disk":
            if dt_options.count("config"):
                if check_count_after_shutdown:
                    raise error.TestFail("See config detached device in "
                                         "xml file after VM shutdown.")
            else:
                if check_count_after_cmd:
                    raise error.TestFail("See device in xml file "
                                         "after detach.")
                if check_vm_after_cmd:
                    raise error.TestFail("See device in VM after detach.")
                if not check_audit_after_cmd:
                    raise error.TestFail("Audit hotunplug failure "
                                         "after detach")

                if dt_options.count("persistent"):
                    if check_count_after_shutdown:
                        raise error.TestFail("See device deattached "
                                             "with persistent after "
                                             "VM shutdown.")
                else:
                    if not check_count_after_shutdown:
                        raise error.TestFail("See non-config detached "
                                             "device in xml file after "
                                             "VM shutdown.")

        else:
            raise error.TestError("Unknown command %s." % test_cmd)
