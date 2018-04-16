import logging
import os
import stat
import subprocess
import time
import socket
import shutil

from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices.controller import Controller
from virttest import data_dir


def run(test, params, env):
    """
    Verify hotplug feature for char device
    """

    vm_name = params.get("main_vm", "vm1")
    status_error = "yes" == params.get("status_error", "no")
    char_dev = params.get("char_dev", "file")
    hotplug_type = params.get("hotplug_type", "qmp")
    dup_charid = "yes" == params.get("dup_charid", "no")
    dup_devid = "yes" == params.get("dup_devid", "no")
    diff_devid = "yes" == params.get("diff_devid", "no")

    tmp_dir = os.path.join(data_dir.get_tmp_dir(), "hotplug_serial")
    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)
    os.chmod(tmp_dir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    # add controller for each char device
    devices = vm_xml.get_devices()
    controllers = vm_xml.get_devices(device_type="controller")
    for dev in controllers:
        if dev.type == "virtio-serial":
            devices.remove(dev)
    controller = Controller("controller")
    controller.type = "virtio-serial"
    controller.index = 0
    devices.append(controller)
    vm_xml.set_devices(devices)
    vm_xml.sync()

    # start and login vm
    vm = env.get_vm(vm_name)
    vm.start()
    session = vm.wait_for_login()

    def prepare_channel_xml(to_file, char_type, id=0):
        params = {}
        mode = ''
        if char_type == "file":
            channel_type = char_type
            channel_path = os.path.join(tmp_dir, char_type)
        elif char_type == "socket":
            channel_type = 'unix'
            channel_path = os.path.join(tmp_dir, char_type)
            mode = 'bind'
        elif char_type == "pty":
            channel_type = char_type
            channel_path = ("/dev/pts/%s" % id)
        params = {'channel_type_name': channel_type,
                  'source_path': channel_path,
                  'source_mode': mode,
                  'target_type': 'virtio',
                  'target_name': char_type}
        channel_xml = utlv.create_channel_xml(params, alias=True, address=True)
        shutil.copyfile(channel_xml.xml, to_file)

    def hotplug_device(type, char_dev, id=0):
        tmp_file = os.path.join(tmp_dir, char_dev)
        if type == "qmp":
            char_add_opt = "chardev-add "
            dev_add_opt = "device_add virtserialport,chardev="
            if char_dev == "file":
                char_add_opt += "file,path=%s,id=file" % tmp_file
                dev_add_opt += "file,name=file,bus=virtio-serial0.0,id=file"
            elif char_dev == "socket":
                char_add_opt += "socket,path=%s,server,nowait,id=socket" % tmp_file
                dev_add_opt += "socket,name=socket,bus=virtio-serial0.0,id=socket"
            elif char_dev == "pty":
                char_add_opt += ("pty,path=/dev/pts/%s,id=pty" % id)
                dev_add_opt += "pty,name=pty,bus=virtio-serial0.0,id=pty"
            result = virsh.qemu_monitor_command(vm_name, char_add_opt, "--hmp")
            if result.exit_status:
                test.error('Failed to add chardev %s to %s. Result:\n %s'
                           % (char_dev, vm_name, result))
            result = virsh.qemu_monitor_command(vm_name, dev_add_opt, "--hmp")
            if result.exit_status:
                test.error('Failed to add device %s to %s. Result:\n %s'
                           % (char_dev, vm_name, result))
        elif type == "attach":
            xml_file = os.path.join(tmp_dir, "xml_%s" % char_dev)
            if char_dev in ["file", "socket"]:
                prepare_channel_xml(xml_file, char_dev)
            elif char_dev == "pty":
                prepare_channel_xml(xml_file, char_dev, id)
            result = virsh.attach_device(vm_name, xml_file)
            # serial device was introduced by the following commit,
            # http://libvirt.org/git/?
            # p=libvirt.git;a=commit;h=b63ea467617e3cbee4282ab2e5e780b4119cef3d
            if "unknown device type" in result.stderr:
                test.cancel('Failed to attach %s to %s. Result:\n %s'
                            % (char_dev, vm_name, result))
        return result

    def dup_hotplug(type, char_dev, id, dup_charid=False, dup_devid=False, diff_devid=False):
        tmp_file = os.path.join(tmp_dir, char_dev)
        if type == "qmp":
            char_add_opt = "chardev-add "
            dev_add_opt = "device_add virtserialport,chardev="
            if char_dev == "file":
                if dup_charid:
                    char_add_opt += "file,path=%s,id=file" % tmp_file
                if dup_devid:
                    dev_add_opt += "file,name=file,bus=virtio-serial0.0,id=file"
                if diff_devid:
                    dev_add_opt += "file,name=file,bus=virtio-serial0.0,id=file1"
            elif char_dev == "socket":
                if dup_charid:
                    char_add_opt += "socket,path=%s,server,nowait,id=socket" % tmp_file
                if dup_devid:
                    dev_add_opt += "socket,name=socket,bus=virtio-serial0.0,id=socket"
                if diff_devid:
                    dev_add_opt += "socket,name=socket,bus=virtio-serial0.0,id=socket1"
            elif char_dev == "pty":
                if dup_charid:
                    char_add_opt += "pty,path=/dev/pts/%s,id=pty" % id
                if dup_devid:
                    dev_add_opt += "pty,name=pty,bus=virtio-serial0.0,id=pty"
                if diff_devid:
                    dev_add_opt += "pty,name=pty,bus=virtio-serial0.0,id=pty1"
            if dup_charid:
                result = virsh.qemu_monitor_command(vm_name, char_add_opt, "--hmp")
            if dup_devid or diff_devid:
                result = virsh.qemu_monitor_command(vm_name, dev_add_opt, "--hmp")
        elif type == "attach":
            if dup_devid:
                result = hotplug_device(type, char_dev, id)
        return result

    def confirm_hotplug_result(char_dev, id=0):
        tmp_file = os.path.join(tmp_dir, char_dev)
        serial_file = os.path.join("/dev/virtio-ports", char_dev)
        result = virsh.qemu_monitor_command(vm_name, "info qtree", "--hmp")
        h_o = result.stdout.strip()
        if not h_o.count("name = \"%s\"" % char_dev):
            test.fail("Cann't find device(%s) from:\n%s" % (char_dev, h_o))
        if char_dev == "file":
            session.cmd("echo test > %s" % serial_file)
            with open(tmp_file, "r") as f:
                r_o = f.read()
        elif char_dev == "socket":
            session.cmd("echo test > /tmp/file")
            sock = socket.socket(socket.AF_UNIX)
            sock.connect(tmp_file)
            session.cmd("dd if=/tmp/file of=%s" % serial_file)
            r_o = sock.recv(1024)
        elif char_dev == "pty":
            session.cmd("echo test > /tmp/file")
            session.cmd("dd if=/tmp/file of=%s &" % serial_file)
            dev_file = "/dev/pts/%s" % id
            if not os.path.exists(dev_file):
                test.fail("%s doesn't exist." % dev_file)
            p = subprocess.Popen(["/usr/bin/cat", dev_file], universal_newlines=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
            session.cmd("echo test >> /tmp/file &")
            while True:
                r_o = p.stdout.readline()
                if r_o or p.poll():
                    break
                time.sleep(0.2)
            p.kill()
        if not r_o.count("test"):
            err_info = "%s device file doesn't match 'test':%s" % (char_dev, r_o)
            test.fail(err_info)

    def unhotplug_serial_device(type, char_dev):
        if type == "qmp":
            del_dev_opt = "device_del %s" % char_dev
            del_char_opt = "chardev-remove %s" % char_dev
            result = virsh.qemu_monitor_command(vm_name, del_dev_opt, "--hmp")
            if result.exit_status:
                test.error('Failed to del device %s from %s.Result:\n%s'
                           % (char_dev, vm_name, result))
            result = virsh.qemu_monitor_command(vm_name, del_char_opt, "--hmp")
        elif type == "attach":
            xml_file = os.path.join(tmp_dir, "xml_%s" % char_dev)
            result = virsh.detach_device(vm_name, xml_file)

    def confirm_unhotplug_result(char_dev):
        serial_file = os.path.join("/dev/virtio-ports", char_dev)
        result = virsh.qemu_monitor_command(vm_name, "info qtree", "--hmp")
        uh_o = result.stdout.strip()
        if uh_o.count("chardev = \"%s\"" % char_dev):
            test.fail("Still can get serial device(%s) from: '%s'"
                      % (char_dev, uh_o))
        if os.path.exists(serial_file):
            test.fail("File '%s' still exists after unhotplug" % serial_file)

    # run test case
    try:
        if char_dev in ['file', 'socket']:
            # if char_dev is file or socket, it doesn't need pts index
            pts_id = 0
        else:
            pts_id = str(utils_misc.aton(utils_misc.get_dev_pts_max_id()) + 1)
            if os.path.exists("/dev/pts/%s" % pts_id):
                test.error('invalid pts index(%s) provided.' % pts_id)
        if status_error:
            hotplug_device(hotplug_type, char_dev, pts_id)
            ret = dup_hotplug(hotplug_type, char_dev, pts_id, dup_charid, dup_devid, diff_devid)
            dup_o = ret.stdout.strip()
            if hotplug_type == "qmp":
                # although it has failed, ret.exit_status will be returned 0.
                err_o1 = "Duplicate ID"
                err_o2 = "Parsing chardev args failed"
                err_o3 = "Property 'virtserialport.chardev' can't"
                if (err_o1 not in dup_o) and (err_o2 not in dup_o) and (err_o3 not in dup_o):
                    test.fail("Expect fail, but run successfully:\n%s" % ret)
            else:
                if "chardev already exists" not in dup_o:
                    logging.info("Expect fail,but run successfully:\n%s" % ret)
        else:
            if char_dev != "all":
                #1.hotplug serial device
                hotplug_device(hotplug_type, char_dev, pts_id)

                #2.confirm hotplug result
                confirm_hotplug_result(char_dev, pts_id)

                #3.unhotplug serial device
                unhotplug_serial_device(hotplug_type, char_dev)

                #4.confirm unhotplug result
                confirm_unhotplug_result(char_dev)
            else:
                #1.hotplug serial device
                hotplug_device(hotplug_type, "file")
                hotplug_device(hotplug_type, "socket")
                hotplug_device(hotplug_type, "pty", pts_id)

                #2.confirm hotplug result
                confirm_hotplug_result("file")
                confirm_hotplug_result("socket")
                confirm_hotplug_result("pty", pts_id)

                #3.unhotplug serial device
                unhotplug_serial_device(hotplug_type, "file")
                unhotplug_serial_device(hotplug_type, "socket")
                unhotplug_serial_device(hotplug_type, "pty")

                #4.confirm unhotplug result
                confirm_unhotplug_result("file")
                confirm_unhotplug_result("socket")
                confirm_unhotplug_result("pty")
    finally:
        vm_xml_backup.sync()
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
