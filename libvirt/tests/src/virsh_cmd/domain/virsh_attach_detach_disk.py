import os
import time
import logging

import aexpect

from avocado.utils import process

from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.staging.service import Factory
from virttest.staging import lv_utils
from virttest.utils_libvirt import libvirt_pcicontr
from virttest import utils_disk
from virttest import utils_misc
from virttest import data_dir
from virttest import libvirt_version


def run(test, params, env):
    """
    Test virsh {at|de}tach-disk command.

    The command can attach new disk/detach disk.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh attach/detach-disk operation.
    3.Recover test environment.
    4.Confirm the test result.
    """
    def check_info_in_audit_log_file(test_cmd, device_source):
        """
        Check if information can be found in audit.log.

        :params test_cmd: test command
        :params device_source: device source
        """
        grep_audit = ('grep "%s" /var/log/audit/audit.log'
                      % test_cmd.split("-")[0])
        cmd = (grep_audit + ' | ' + 'grep "%s" | tail -n1 | grep "res=success"'
               % device_source)
        return process.run(cmd, ignore_status=True, shell=True).exit_status == 0

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
                new_parts = utils_disk.get_parts_list(session)
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

    def check_shareable(at_with_shareable, test_twice):
        """
        check if current libvirt version support shareable option

        at_with_shareable: True or False. Whether attach disk with shareable option
        test_twice: True or False. Whether perform operations twice
        return: True or cancel the test
        """
        if at_with_shareable or test_twice:
            if libvirt_version.version_compare(3, 9, 0):
                return True
            else:
                test.cancel("Current libvirt version doesn't support shareable feature")

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
    test_systemlink_twice = "yes" == params.get("at_dt_disk_twice_with_systemlink", "no")
    test_type = "yes" == params.get("at_dt_disk_check_type", "no")
    test_audit = "yes" == params.get("at_dt_disk_check_audit", "no")
    test_block_dev = "yes" == params.get("at_dt_disk_iscsi_device", "no")
    test_logcial_dev = "yes" == params.get("at_dt_disk_logical_device", "no")
    restart_libvirtd = "yes" == params.get("at_dt_disk_restart_libvirtd", "no")
    detach_disk_with_print_xml = "yes" == params.get("detach_disk_with_print_xml", "no")
    vg_name = params.get("at_dt_disk_vg", "vg_test_0")
    lv_name = params.get("at_dt_disk_lv", "lv_test_0")
    # Get additional lvm item names.
    additional_lv_names = params.get("at_dt_disk_additional_lvs", "").split()
    serial = params.get("at_dt_disk_serial", "")
    address = params.get("at_dt_disk_address", "")
    address2 = params.get("at_dt_disk_address2", "")
    cache_options = params.get("cache_options", "")
    time_sleep = params.get("time_sleep", 3)
    # Define one empty list to locate those lvm.
    total_lvm_names = []
    if check_shareable(at_with_shareable, test_twice):
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

    machine_type = params.get('machine_type')
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    # Start vm and get all partions in vm.
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_parts = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Back up xml file.
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vm_dump_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Create virtual device file.
    device_source_path = os.path.join(data_dir.get_data_dir(), device_source_name)
    if test_block_dev:
        device_source = libvirt.setup_or_cleanup_iscsi(True)
        if not device_source:
            # We should skip this case
            test.cancel("Can not get iscsi device name in host")
        if test_logcial_dev:
            if lv_utils.vg_check(vg_name):
                lv_utils.vg_remove(vg_name)
            lv_utils.vg_create(vg_name, device_source)
            device_source = libvirt.create_local_disk("lvm",
                                                      size="10M",
                                                      vgname=vg_name,
                                                      lvname=lv_name)
            logging.debug("New created volume: %s", lv_name)
            total_lvm_names.append(device_source)
            if test_systemlink_twice:
                for lvm__item_name in additional_lv_names:
                    additional_device_source = libvirt.create_local_disk(
                        "lvm", size="10M", vgname=vg_name, lvname=lvm__item_name)
                    logging.debug("New created volume: %s", additional_device_source)
                    total_lvm_names.append(additional_device_source)
    else:
        if source_path and create_img:
            device_source = libvirt.create_local_disk(
                "file", path=device_source_path,
                size="1G", disk_format=device_source_format)
        else:
            device_source = device_source_name

    if machine_type == 'q35':
        # Add more pci controllers to avoid error: No more available PCI slots
        if test_twice and params.get("add_more_pci_controllers", "yes") == "yes":
            vm_dump_xml.remove_all_device_by_type('controller')
            machine_list = vm_dump_xml.os.machine.split("-")
            vm_dump_xml.set_os_attrs(**{"machine": machine_list[0] + "-q35-" + machine_list[2]})
            q35_pcie_dict0 = {'controller_model': 'pcie-root', 'controller_type': 'pci', 'controller_index': 0}
            q35_pcie_dict1 = {'controller_model': 'pcie-root-port', 'controller_type': 'pci'}
            vm_dump_xml.add_device(libvirt.create_controller_xml(q35_pcie_dict0))
            # Add enough controllers to match multiple times disk attaching requirements
            for i in list(range(1, 15)):
                q35_pcie_dict1.update({'controller_index': "%d" % i})
                vm_dump_xml.add_device(libvirt.create_controller_xml(q35_pcie_dict1))
            vm_dump_xml.sync()

    if params.get("reset_pci_controllers_nums", "no") == "yes" \
            and 'attach-disk' in test_cmd:
        libvirt_pcicontr.reset_pci_num(vm_name, 15)

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
        #Since lock feature is introduced in libvirt 3.9.0 afterwards, disk shareable options
        #need be set if disk needs be attached multitimes
        if check_shareable(at_with_shareable, test_twice):
            s_at_options += " --mode shareable"

        s_attach = virsh.attach_disk(vm_name, device_source, device_target,
                                     s_at_options, debug=True).exit_status
        if s_attach != 0:
            logging.error("Attaching device failed before testing detach-disk")
        else:
            logging.debug("Attaching device succeeded before testing detach-disk")
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
        test.error("Add acpiphp module failed before test.")

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
        # For detach disk with print-xml option, it only print information,and not actual disk detachment.
        if detach_disk_with_print_xml and libvirt_version.version_compare(4, 5, 0):
            ret = virsh.detach_disk(vm_ref, device_target, at_options)
            libvirt.check_exit_status(ret)
            cmd = ("echo \"%s\" | grep -A 16 %s"
                   % (ret.stdout.strip(), device_source_name))
            if process.system(cmd, ignore_status=True, shell=True):
                test.error("Check disk with source image name failed")
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
    if test_systemlink_twice:
        # Detach lvm previously attached.
        result = virsh.detach_disk(vm_ref, device_target, dt_options,
                                   debug=True)
        libvirt.check_exit_status(result)
        # Remove systemlink for lv01,lv02,and lv03
        for lvm_item in total_lvm_names:
            remove_systemlink_cmd = ('lvchange -a n %s' % lvm_item)
            if process.run(remove_systemlink_cmd, shell=True).exit_status:
                logging.error("Remove systemlink failed")
        # Add new systemlink for lv01,lv02,and lv03 by shifting one position
        for index in range(0, len(total_lvm_names)):
            add_systemlink_cmd = ('lvchange -a y %s' % total_lvm_names[(index+1) % len(total_lvm_names)])
            if process.run(add_systemlink_cmd, shell=True).exit_status:
                logging.error("Add systemlink failed")
        # Attach lvm lv01 again.
        result = virsh.attach_disk(vm_ref, device_source,
                                   device_target, at_options,
                                   debug=True)
        libvirt.check_exit_status(result)
        # Detach lvm 01 again.
        result = virsh.detach_disk(vm_ref, device_target, dt_options,
                                   debug=True)
        libvirt.check_exit_status(result)

    # Resume guest after command. On newer libvirt this is fixed as it has
    # been a bug. The change in xml file is done after the guest is resumed.
    if pre_vm_state == "paused":
        vm.resume()
        time.sleep(5)

    # Check audit log
    check_audit_after_cmd = True
    if test_audit:
        result = utils_misc.wait_for(lambda: check_info_in_audit_log_file(test_cmd, device_source), timeout=20)
        if not result:
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
    save_file = os.path.join(data_dir.get_data_dir(), "vm.save")
    try:
        if eject_cdrom:
            eject_params = {'type_name': "file", 'device_type': "cdrom",
                            'target_dev': device_target, 'target_bus': device_disk_bus}
            eject_xml = libvirt.create_disk_xml(eject_params)
            with open(eject_xml) as eject_file:
                logging.debug("Eject CDROM by XML: %s", eject_file.read())
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
                test.fail("Eject CDROM failed")
            if vm_xml.VMXML.check_disk_exist(vm_name, device_source):
                test.fail("Find %s after do eject" % device_source)
        # Save and restore VM
        if save_vm:
            result = virsh.save(vm_name, save_file, debug=True)
            libvirt.check_exit_status(result)
            result = virsh.restore(save_file, debug=True)
            libvirt.check_exit_status(result)
            if vm_xml.VMXML.check_disk_exist(vm_name, device_source):
                test.fail("Find %s after do restore" % device_source)

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
        logging.debug("Restore the VM XML")
        backup_xml.sync()
        if os.path.exists(save_file):
            os.remove(save_file)
        if test_block_dev:
            if test_logcial_dev:
                libvirt.delete_local_disk("lvm", vgname=vg_name, lvname=lv_name)
                if test_systemlink_twice:
                    for lv_item_name in additional_lv_names:
                        libvirt.delete_local_disk("lvm", vgname=vg_name, lvname=lv_item_name)
                lv_utils.vg_remove(vg_name)
                process.run("pvremove %s" % device_source, shell=True, ignore_status=True)
            libvirt.setup_or_cleanup_iscsi(False)
        else:
            libvirt.delete_local_disk("file", device_source)

    # Check results.
    if status_error:
        if not status:
            test.fail("virsh %s exit with unexpected value."
                      % test_cmd)
    else:
        if test_systemlink_twice:
            return
        if status:
            test.fail("virsh %s failed." % test_cmd)
        if test_cmd == "attach-disk":
            if at_options.count("config"):
                if not check_count_after_shutdown:
                    test.fail("Cannot see config attached device "
                              "in xml file after VM shutdown.")
                if not check_disk_serial:
                    test.fail("Serial set failed after attach")
                if not check_disk_address:
                    test.fail("Address set failed after attach")
                if not check_disk_address2:
                    test.fail("Address(multifunction) set failed"
                              " after attach")
            else:
                if not check_count_after_cmd:
                    test.fail("Cannot see device in xml file"
                              " after attach.")
                if not check_vm_after_cmd:
                    test.fail("Cannot see device in VM after"
                              " attach.")
                if not check_disk_type:
                    test.fail("Check disk type failed after"
                              " attach.")
                if not check_audit_after_cmd:
                    test.fail("Audit hotplug failure after attach")
                if not check_cache_after_cmd:
                    test.fail("Check cache failure after attach")
                if at_options.count("persistent"):
                    if not check_count_after_shutdown:
                        test.fail("Cannot see device attached "
                                  "with persistent after "
                                  "VM shutdown.")
                else:
                    if check_count_after_shutdown:
                        test.fail("See non-config attached device "
                                  "in xml file after VM shutdown.")
        elif test_cmd == "detach-disk":
            if dt_options.count("config"):
                if check_count_after_shutdown:
                    test.fail("See config detached device in "
                              "xml file after VM shutdown.")
            else:
                if check_count_after_cmd:
                    test.fail("See device in xml file "
                              "after detach.")
                if check_vm_after_cmd:
                    test.fail("See device in VM after detach.")
                if not check_audit_after_cmd:
                    test.fail("Audit hotunplug failure "
                              "after detach")

                if dt_options.count("persistent"):
                    if check_count_after_shutdown:
                        test.fail("See device deattached "
                                  "with persistent after "
                                  "VM shutdown.")
                else:
                    if not check_count_after_shutdown:
                        test.fail("See non-config detached "
                                  "device in xml file after "
                                  "VM shutdown.")

        else:
            test.error("Unknown command %s." % test_cmd)
