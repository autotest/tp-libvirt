import os
import shutil
import logging

from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest import utils_test
from virttest import utils_misc
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.input import Input


def run(test, params, env):
    """
    Stress test for the hotplug feature of usb device.
    """
    # get the params from params
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    keyboard = "yes" == params.get("usb_hotplug_keyboard", "no")
    mouse = "yes" == params.get("usb_hotplug_mouse", "no")
    tablet = "yes" == params.get("usb_hotplug_tablet", "no")
    disk = "yes" == params.get("usb_hotplug_disk", "no")

    attach_count = int(params.get("attach_count", "1"))
    attach_type = params.get("attach_type", "attach_device")
    bench_type = params.get("guest_bench", None)
    control_file = params.get("control_file", None)

    status_error = ("yes" == params.get("status_error", "no"))

    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    tmp_dir = os.path.join(data_dir.get_tmp_dir(), "usb_hotplug_files")

    if control_file is not None:
        params["test_control_file"] = control_file
        params["main_vm"] = vm_name
        control_path = os.path.join(test.virtdir, "control",
                                    control_file)

        session = vm.wait_for_login()
        command = utils_test.run_autotest(vm, session, control_path,
                                          None, None,
                                          params, copy_only=True)
        session.cmd("%s &" % command)

        def _is_iozone_running():
            session_tmp = vm.wait_for_login()
            return (not session_tmp.cmd_status("ps -ef|grep iozone|grep -v grep"))

        def _is_stress_running():
            session_tmp = vm.wait_for_login()
            return (not session_tmp.cmd_status("ps -ef|grep stress|grep -v grep"))
        if bench_type == "stress":
            if not utils_misc.wait_for(_is_stress_running, timeout=160):
                test.cancel("Failed to run stress in guest.\n"
                            "Since we need to run a autotest of iozone "
                            "in guest, so please make sure there are "
                            "some necessary packages in guest,"
                            "such as gcc, tar, bzip2")
        elif bench_type == "iozone":
            if not utils_misc.wait_for(_is_iozone_running, timeout=160):
                test.cancel("Failed to run iozone in guest.\n"
                            "Since we need to run a autotest of iozone "
                            "in guest, so please make sure there are "
                            "some necessary packages in guest,"
                            "such as gcc, tar, bzip2")
        logging.debug("bench is already running in guest.")
    try:
        try:
            result = None
            disk_xml = None
            tablet_xml = None
            mouse_xml = None
            if not os.path.isdir(tmp_dir):
                os.mkdir(tmp_dir)
            for i in range(attach_count):
                path = os.path.join(tmp_dir, "%s.img" % i)
                if attach_type == "qemu_monitor":
                    options = "--hmp"
                    if disk:
                        utils_test.libvirt.create_local_disk("file", path, size="1M")
                        attach_cmd = "drive_add"
                        attach_cmd += (" 0 id=drive-usb-disk%s,if=none,file=%s" % (i, path))

                        result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=options)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if keyboard:
                        attach_cmd = "device_add"
                        attach_cmd += " usb-kdb,bus=usb1.0,id=kdb"

                        result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=options)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if mouse:
                        attach_cmd = "device_add"
                        attach_cmd += " usb-mouse,bus=usb1.0,id=mouse"

                        result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=options)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if tablet:
                        attach_cmd = "device_add"
                        attach_cmd += " usb-tablet,bus=usb1.0,id=tablet"

                        result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=options)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                else:
                    if disk:
                        utils_test.libvirt.create_local_disk("file", path, size="1M")
                        os.chmod(path, 0o666)
                        disk_xml = Disk(type_name="file")
                        disk_xml.device = "disk"
                        disk_xml.source = disk_xml.new_disk_source(**{"attrs": {'file': path}})
                        disk_xml.driver = {"name": "qemu", "type": 'raw', "cache": "none"}
                        disk_xml.target = {"dev": 'sdb', "bus": "usb"}

                        attributes = {'type_name': "usb", 'bus': "1", 'port': "0"}
                        disk_xml.address = disk_xml.new_disk_address(**{"attrs": attributes})

                        result = virsh.attach_device(vm_name, disk_xml.xml)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if mouse:
                        mouse_xml = Input("mouse")
                        mouse_xml.input_bus = "usb"
                        attributes = {'type_name': "usb", 'bus': "1", 'port': "0"}
                        mouse_xml.address = mouse_xml.new_input_address(**{"attrs": attributes})

                        result = virsh.attach_device(vm_name, mouse_xml.xml)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if tablet:
                        tablet_xml = Input("tablet")
                        tablet_xml.input_bus = "usb"
                        attributes = {'type_name': "usb", 'bus': "1", 'port': "0"}
                        tablet_xml.address = tablet_xml.new_input_address(**{"attrs": attributes})

                        result = virsh.attach_device(vm_name, tablet_xml.xml)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if keyboard:
                        kbd_xml = Input("keyboard")
                        kbd_xml.input_bus = "usb"
                        attributes = {'type_name': "usb", 'bus': "1", 'port': "0"}
                        kbd_xml.address = kbd_xml.new_input_address(**{"attrs": attributes})

                        result = virsh.attach_device(vm_name, kbd_xml.xml)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)

                if attach_type == "qemu_monitor":
                    options = "--hmp"
                    if disk:
                        attach_cmd = "drive_del"
                        attach_cmd += (" drive-usb-disk")

                        result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=options)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if mouse:
                        attach_cmd = "device_del"
                        attach_cmd += (" mouse")

                        result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=options)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if keyboard:
                        attach_cmd = "device_del"
                        attach_cmd += (" keyboard")

                        result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=options)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if tablet:
                        attach_cmd = "device_del"
                        attach_cmd += (" tablet")

                        result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=options)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                else:
                    if disk:
                        result = virsh.detach_device(vm_name, disk_xml.xml)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if mouse:
                        result = virsh.detach_device(vm_name, mouse_xml.xml)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if keyboard:
                        result = virsh.detach_device(vm_name, kbd_xml.xml)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
                    if tablet:
                        result = virsh.detach_device(vm_name, tablet_xml.xml)
                        if result.exit_status:
                            raise process.CmdError(result.command, result)
        except process.CmdError as e:
            if not status_error:
                test.fail("failed to attach device.\n"
                          "Detail: %s." % result)
    finally:
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir)
        vm_xml_backup.sync()
