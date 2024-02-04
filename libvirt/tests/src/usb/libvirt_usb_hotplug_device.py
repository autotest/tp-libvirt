import os
import shutil

from aexpect import ShellError
from aexpect import ShellTimeoutError

from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest import utils_misc
from virttest import utils_selinux
from virttest.remote import LoginError
from virttest.utils_test import libvirt
from virttest.virt_vm import VMError
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.input import Input


def run(test, params, env):
    """
    Test for hotplug usb device.
    """
    # get the params from params
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    usb_type = params.get("usb_type", "kbd")
    attach_type = params.get("attach_type", "attach_device")
    attach_count = int(params.get("attach_count", "1"))
    if usb_type == "storage":
        model = params.get("model", "nec-xhci")
        index = params.get("index", "1")
    status_error = ("yes" == params.get("status_error", "no"))

    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    # Set selinux of host.
    backup_sestatus = utils_selinux.get_status()
    utils_selinux.set_status("permissive")

    if usb_type == "storage":
        controllers = vm_xml.get_devices(device_type="controller")
        devices = vm_xml.get_devices()
        for dev in controllers:
            if dev.type == "usb" and dev.index == "1":
                devices.remove(dev)
        controller = Controller("controller")
        controller.type = "usb"
        controller.index = index
        controller.model = model
        devices.append(controller)
        vm_xml.set_devices(devices)

    try:
        session = vm.wait_for_login()
    except (LoginError, VMError, ShellError) as e:
        test.fail("Test failed: %s" % str(e))

    def is_hotplug_ok():
        try:
            output = session.cmd_output("fdisk -l | grep -c '^Disk /dev/.* 1 M'")
            if int(output.strip()) != attach_count:
                return False
            else:
                return True
        except ShellTimeoutError as detail:
            test.fail("unhotplug failed: %s, " % detail)

    tmp_dir = os.path.join(data_dir.get_tmp_dir(), "usb_hotplug_files")
    if not os.path.isdir(tmp_dir):
        os.mkdir(tmp_dir)

    try:
        result = None
        dev_xml = None
        opt = "--hmp"
        for i in range(attach_count):
            if usb_type == "storage":
                path = os.path.join(tmp_dir, "%s.img" % i)
                libvirt.create_local_disk("file", path, size="1M", disk_format="qcow2")
                os.chmod(path, 0o666)

            if attach_type == "qemu_monitor":
                if usb_type == "storage":
                    attach_cmd = "drive_add"
                    attach_cmd += (" 0 id=drive-usb-%s,if=none,file=%s" % (i, path))

                    result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=opt)
                    if result.exit_status or (result.stdout.strip().find("OK") == -1):
                        raise process.CmdError(result.command, result)

                    attach_cmd = "device_add usb-storage,"
                    attach_cmd += ("id=drive-usb-%s,bus=usb1.0,drive=drive-usb-%s" % (i, i))
                else:
                    attach_cmd = "device_add"
                    attach_cmd += " usb-%s,bus=usb1.0,id=%s%s" % (usb_type, usb_type, i)

                result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=opt)
                if result.exit_status:
                    raise process.CmdError(result.command, result)
            else:
                attributes = {'type_name': "usb", 'bus': "1", 'port': "0"}
                if usb_type == "storage":
                    dev_xml = Disk(type_name="file")
                    dev_xml.device = "disk"
                    dev_xml.source = dev_xml.new_disk_source(**{"attrs": {'file': path}})
                    dev_xml.driver = {"name": "qemu", "type": 'qcow2', "cache": "none"}
                    dev_xml.target = {"dev": 'sdb', "bus": "usb"}
                    dev_xml.address = dev_xml.new_disk_address(**{"attrs": attributes})
                else:
                    if usb_type == "mouse":
                        dev_xml = Input("mouse")
                    elif usb_type == "tablet":
                        dev_xml = Input("tablet")
                    else:
                        dev_xml = Input("keyboard")

                    dev_xml.input_bus = "usb"
                    dev_xml.address = dev_xml.new_input_address(**{"attrs": attributes})

                result = virsh.attach_device(vm_name, dev_xml.xml)
                if result.exit_status:
                    raise process.CmdError(result.command, result)

        if status_error and usb_type == "storage":
            if utils_misc.wait_for(is_hotplug_ok, timeout=30):
                # Sometimes we meet an error but the ret in $? is 0.
                test.fail("\nAttach device successfully in negative case."
                          "\nExcept it fail when attach count exceed maximum."
                          "\nDetail: %s" % result)

        for i in range(attach_count):
            attach_cmd = "device_del"
            if attach_type == "qemu_monitor":
                if usb_type == "storage":
                    attach_cmd += (" drive-usb-%s" % i)
                else:
                    if usb_type == "mouse":
                        attach_cmd += " mouse"
                    elif usb_type == "tablet":
                        attach_cmd += " tablet"
                    else:
                        attach_cmd += " keyboard"

                result = virsh.qemu_monitor_command(vm_name, attach_cmd, options=opt)
                if result.exit_status:
                    raise process.CmdError(result.command, result)
            else:
                result = virsh.detach_device(vm_name, dev_xml.xml)
                if result.exit_status:
                    raise process.CmdError(result.command, result)
    except process.CmdError as e:
        if not status_error:
            # live attach of device 'input' is not supported
            ret = result.stderr.find("Operation not supported")
            if usb_type != "storage" and ret > -1:
                pass
            else:
                test.fail("failed to attach device.\nDetail: %s." % result)
    finally:
        session.close()
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir)
        utils_selinux.set_status(backup_sestatus)
        vm_xml_backup.sync()
