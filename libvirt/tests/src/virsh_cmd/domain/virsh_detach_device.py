import os
import time
import logging
import shutil

import aexpect

from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest import data_dir
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test virsh detach-device command.

    The command can detach disk.
    1.Prepare test environment,destroy or suspend a VM.
    2.Perform virsh detach-device operation.
    3.Recover test environment.
    4.Confirm the test result.
    """

    def create_device_file(device_source="/tmp/attach.img"):
        """
        Create a device source file.

        :param device_source: Device source file.
        """
        try:
            with open(device_source, 'wb') as device_file:
                device_file.seek((512 * 1024 * 1024) - 1)
                device_file.write(str(0).encode())
        except IOError:
            logging.error("Image file %s created failed.", device_source)

    def check_vm_partition(vm, device, os_type, target_name):
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
            if os_type == "linux":
                session = vm.wait_for_login()
                if device == "disk":
                    s, o = session.cmd_status_output("grep %s /proc/partitions"
                                                     % target_name)
                    logging.info("Virtio devices in VM:\n%s", o)
                elif device == "cdrom":
                    s, o = session.cmd_status_output("ls /dev/cdrom")
                    logging.info("CDROM in VM:\n%s", o)
                elif device == "iface":
                    s, o = session.cmd_status_output("ls /")
                session.close()
                if s != 0:
                    return False
            return True
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
                s_rpm, _ = session.cmd_status_output("rpm --version")
                # If status is different from 0, this
                # guest OS doesn't support the rpm package
                # manager
                if s_rpm:
                    session.close()
                    return True
                _, o_vd = session.cmd_status_output("rpm -qa | grep"
                                                    " redhat-release")
                if o_vd.find("5Server") != -1:
                    s_mod, _ = session.cmd_status_output("modprobe acpiphp")
                    if s_mod != 0:
                        session.close()
                        return False
                session.close()
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as e:
            logging.error(str(e))
            return False

    def create_device_xml(params, xml_path, device_source):
        """
        Create a xml file for device
        """
        device_xml_name = params.get("dt_device_xml", "device.xml")
        device_xml_file = os.path.join(xml_path, device_xml_name)
        device_type = params.get("dt_device_device", "disk")
        if device_type in ["disk", 'cdrom']:
            disk_class = vm_xml.VMXML.get_device_class('disk')
            if test_block_dev:
                disk = disk_class(type_name='block')
                stype = 'dev'
            else:
                disk = disk_class(type_name='file')
                stype = 'file'
            disk.device = device_type
            disk.driver = dict(name='qemu', type='raw')
            disk.source = disk.new_disk_source(attrs={stype: device_source})
            disk.target = dict(bus=device_bus, dev=device_target)
            disk.xmltreefile.write()
            shutil.copyfile(disk.xml, device_xml_file)
        else:
            iface_class = vm_xml.VMXML.get_device_class('interface')
            iface = iface_class(type_name='network')
            iface.mac_address = iface_mac_address
            iface.source = dict(network=iface_network)
            iface.model = iface_model_type
            iface.xmltreefile.write()
            shutil.copyfile(iface.xml, device_xml_file)
        return device_xml_file

    vm_ref = params.get("dt_device_vm_ref", "name")
    dt_options = params.get("dt_device_options", "")
    pre_vm_state = params.get("dt_device_pre_vm_state", "running")
    status_error = "yes" == params.get("status_error", 'no')
    no_attach = "yes" == params.get("dt_device_no_attach", 'no')
    os_type = params.get("os_type", "linux")
    device = params.get("dt_device_device", "disk")
    readonly = "yes" == params.get("detach_readonly", "no")
    test_cmd = "detach-device"
    if not virsh.has_command_help_match(test_cmd, dt_options) and\
       not status_error:
        test.cancel("Current libvirt version doesn't support '%s'"
                    " for %s" % (dt_options, test_cmd))

    # Disk specific attributes.
    device_source_name = params.get("dt_device_device_source", "attach.img")
    device_target = params.get("dt_device_device_target", "vdd")
    device_bus = params.get("dt_device_bus_type")
    test_block_dev = "yes" == params.get("dt_device_iscsi_device", "no")

    # interface specific attributes.
    iface_network = params.get("dt_device_iface_network")
    iface_model_type = params.get("dt_device_iface_model_type")
    iface_mac_address = params.get("dt_device_iface_mac_address")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    if vm.is_alive():
        vm.destroy(gracefully=False)

    # Back up xml file.
    backup_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    device_source = os.path.join(data_dir.get_tmp_dir(), device_source_name)

    # Create virtual device file.
    if test_block_dev:
        device_source = libvirt.setup_or_cleanup_iscsi(True)
        if not device_source:
            # We should skip this case
            test.cancel("Can not get iscsi device name in host")
    else:
        create_device_file(device_source)

    try:
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # If we are testing cdrom device, we need to detach hdc in VM first.
        if device == "cdrom":
            virsh.detach_disk(vm_name, device_target, "--config",
                              ignore_status=True)

        device_xml = create_device_xml(params, test.virtdir, device_source)
        if not no_attach:
            s_attach = virsh.attach_device(vm_name, device_xml,
                                           flagstr="--config").exit_status
            if s_attach != 0:
                logging.error("Attach device failed before testing "
                              "detach-device")

        vm.start()
        vm.wait_for_serial_login()

        # Add acpiphp module before testing if VM's os type is rhle5.*
        if device in ['disk', 'cdrom']:
            if not acpiphp_module_modprobe(vm, os_type):
                test.error("Add acpiphp module failed before test.")

        # Turn VM into certain state.
        if pre_vm_state == "paused":
            logging.info("Suspending %s...", vm_name)
            if vm.is_alive():
                vm.pause()
        elif pre_vm_state == "shut off":
            logging.info("Shutting down %s...", vm_name)
            if vm.is_alive():
                vm.destroy(gracefully=False)

        # Get disk count before test.
        if device in ['disk', 'cdrom']:
            device_count_before_cmd = vm_xml.VMXML.get_disk_count(vm_name)
        else:
            vm_cls = vm_xml.VMXML.new_from_dumpxml(vm_name)
            device_count_before_cmd = len(vm_cls.devices)

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

        status = virsh.detach_device(vm_ref, device_xml, readonly=readonly, flagstr=dt_options,
                                     debug=True).exit_status

        time.sleep(2)
        # Resume guest after command. On newer libvirt this is fixed as it has
        # been a bug. The change in xml file is done after the guest is
        # resumed.
        if pre_vm_state == "paused":
            vm.resume()

        # Check disk count after command.
        check_count_after_cmd = True
        if device in ['disk', 'cdrom']:
            device_count_after_cmd = vm_xml.VMXML.get_disk_count(vm_name)
        else:
            vm_cls = vm_xml.VMXML.new_from_dumpxml(vm_name)
            device_count_after_cmd = len(vm_cls.devices)
        if device_count_after_cmd < device_count_before_cmd:
            check_count_after_cmd = False

        # Recover VM state.
        if pre_vm_state == "shut off" and device in ['disk', 'cdrom']:
            vm.start()

        # Check in VM after command.
        check_vm_after_cmd = True
        if device in ['disk', 'cdrom']:
            check_vm_after_cmd = check_vm_partition(vm, device, os_type,
                                                    device_target)

        # Destroy VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Check disk count after VM shutdown (with --config).
        check_count_after_shutdown = True
        if device in ['disk', 'cdrom']:
            device_count_after_shutdown = vm_xml.VMXML.get_disk_count(vm_name)
        else:
            vm_cls = vm_xml.VMXML.new_from_dumpxml(vm_name)
            device_count_after_shutdown = len(vm_cls.devices)
        if device_count_after_shutdown < device_count_before_cmd:
            check_count_after_shutdown = False
    finally:
        # Recover VM.
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
        if test_block_dev:
            libvirt.setup_or_cleanup_iscsi(False)
        elif os.path.exists(device_source):
            os.remove(device_source)

    # Check results.
    if status_error:
        if not status:
            test.fail("detach-device exit with unexpected value.")
    else:
        if status:
            test.fail("virsh detach-device failed.")
        if dt_options.count("config"):
            if check_count_after_shutdown:
                test.fail("See config detached device in "
                          "xml file after VM shutdown.")
            if pre_vm_state == "shut off":
                if check_count_after_cmd:
                    test.fail("See device in xml after detach with"
                              " --config option")
            elif pre_vm_state == "running":
                if not check_vm_after_cmd and device in ['disk', 'cdrom']:
                    test.fail("Cannot see device in VM after"
                              " detach with '--config' option"
                              " when VM is running.")

        elif dt_options.count("live"):
            if check_count_after_cmd:
                test.fail("See device in xml after detach with"
                          "--live option")
            if not check_count_after_shutdown:
                test.fail("Cannot see config detached device in"
                          " xml file after VM shutdown with"
                          " '--live' option.")
            if check_vm_after_cmd and device in ['disk', 'cdrom']:
                test.fail("See device in VM with '--live' option"
                          " when VM is running")
        elif dt_options.count("current"):
            if check_count_after_cmd:
                test.fail("See device in xml after detach with"
                          " --current option")
            if pre_vm_state == "running":
                if not check_count_after_shutdown:
                    test.fail("Cannot see config detached device in"
                              " xml file after VM shutdown with"
                              " '--current' option.")
                if check_vm_after_cmd and device in ['disk', 'cdrom']:
                    test.fail("See device in VM with '--live'"
                              " option when VM is running")
        elif dt_options.count("persistent"):
            if check_count_after_shutdown:
                test.fail("See device deattached with "
                          "'--persistent' option after "
                          "VM shutdown.")
