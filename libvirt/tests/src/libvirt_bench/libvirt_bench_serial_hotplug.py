import os
import stat
import logging
import socket
import subprocess
import time
import shutil
from autotest.client.shared import error
from virttest import virsh, libvirt_vm, utils_test, utils_misc
from virttest.libvirt_xml.devices import channel


def run(test, params, env):
    """
    Stress test for the hotplug feature of serial device
    """

    vm_name = params.get("main_vm", "vm1")
    status_error = "yes" == params.get("status_error", "no")
    char_dev = params.get("char_dev", "file")
    hotplug_type = params.get("hotplug_type", "qmp")
    load_type = params.get("load_type", "")
    load_params = params.get("load_params", "")
    test_count = int(params.get("test_count", 5))
    test_type = params.get("test_type", "multi")

    tmp_dir = os.path.join(test.tmpdir, "hotplug_serial_load")
    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)
    os.chmod(tmp_dir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)

    load_vms = []
    if load_type in ['cpu', 'memory', 'io']:
        params["stress_args"] = load_params
    load_vms.append(libvirt_vm.VM(vm_name, params, test.bindir,
                                  env.get("address_cache")))

    vm = env.get_vm(vm_name)
    session = vm.wait_for_login()

    def create_channel_xml(vm_name, char_type, index=1, id=0):
        """
        Create a XML contains channel information.
        """
        channel_type = char_type
        if char_type == "file":
            channel_path = ("%s/%s%s" % (tmp_dir, char_type, index))
            channel_source = {'path': channel_path}
        if char_type == "socket":
            channel_type = 'unix'
            channel_path = ("%s/%s%s" % (tmp_dir, char_type, index))
            channel_source = {'mode': 'bind', 'path': channel_path}
        if char_type == "pty":
            channel_path = ("/dev/pts/%s" % id)
            channel_source = {'path': channel_path}

        target_name = "%s%s" % (char_type, index)
        channel_target = {'type': 'virtio', 'name': target_name}
        channel_alias = {'name': target_name}
        channel_address = {'type': 'virtio-serial', 'controller': '0', 'bus': '0'}
        channel_params = {'type_name': channel_type, 'source': channel_source,
                          'target': channel_target, 'alias': channel_alias,
                          'address': channel_address}
        channelxml = channel.Channel.new_from_dict(channel_params)
        logging.debug("Channel XML:\n%s", channelxml)

        xmlf = open(channelxml.xml)
        try:
            xml_lines = xmlf.read()
        finally:
            xmlf.close()
        return xml_lines

    def hotplug_device(hotplug_type, char_dev, index=1, id=0):
        if hotplug_type == "qmp":
            char_add_opt = "chardev-add "
            dev_add_opt = "device_add virtserialport,chardev="
            if char_dev == "file":
                char_add_opt += ("file,path=%s/file%s,id=file%s"
                                 % (tmp_dir, index, index))
                dev_add_opt += ("file%s,name=file%s,bus=virtio-serial0.0,id=file%s"
                                % (index, index, index))
            elif char_dev == "socket":
                char_add_opt += ("socket,path=%s/socket%s,server,nowait,id=socket%s"
                                 % (tmp_dir, index, index))
                dev_add_opt += ("socket%s,name=socket%s,bus=virtio-serial0.0,id=socket%s"
                                % (index, index, index))
            elif char_dev == "pty":
                char_add_opt += "pty,path=/dev/pts/%s,id=pty%s" % (id, index)
                dev_add_opt += ("pty%s,name=pty%s,bus=virtio-serial0.0,id=pty%s"
                                % (index, index, index))
            virsh.qemu_monitor_command(vm_name, char_add_opt, "--hmp")
            virsh.qemu_monitor_command(vm_name, dev_add_opt, "--hmp")
        elif hotplug_type == "attach":
            if char_dev in ["file", "socket"]:
                xml_info = create_channel_xml(vm_name, char_dev, index)
            elif char_dev == "pty":
                xml_info = create_channel_xml(vm_name, char_dev, index, id)

            xmlf = "%s/xml_%s%s" % (tmp_dir, char_dev, index)
            f = open(xmlf, "w")
            f.write(xml_info)
            f.close()
            virsh.attach_device(vm_name, xmlf, flagstr="--live")

    def confirm_hotplug_result(char_dev, index=1, id=0):
        result = virsh.qemu_monitor_command(vm_name, "info qtree", "--hmp")
        h_o = result.stdout.strip()
        chardev_c = h_o.count("chardev = %s%s" % (char_dev, index))
        name_c = h_o.count("name = \"%s%s\"" % (char_dev, index))
        if chardev_c == 0 and name_c == 0:
            raise error.TestFail("Cannot get serial device info: '%s'" % h_o)

        tmp_file = "%s/%s%s" % (tmp_dir, char_dev, index)
        serial_file = "/dev/virtio-ports/%s%s" % (char_dev, index)
        if char_dev == "file":
            session.cmd("echo test > %s" % serial_file)
            f = open(tmp_file, "r")
            output = f.read()
            f.close()
        elif char_dev == "socket":
            session.cmd("echo test > /tmp/file")
            sock = socket.socket(socket.AF_UNIX)
            sock.connect(tmp_file)
            session.cmd("dd if=/tmp/file of=%s" % serial_file)
            output = sock.recv(1024)
            sock.close()
        elif char_dev == "pty":
            session.cmd("echo test > /tmp/file")
            session.cmd("dd if=/tmp/file of=%s &" % serial_file)
            dev_file = "/dev/pts/%s" % id
            if not os.path.exists(dev_file):
                raise error.TestFail("%s doesn't exist." % dev_file)
            p = subprocess.Popen(["/usr/bin/cat", dev_file],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            while True:
                output = p.stdout.readline()
                if output or p.poll():
                    break
                time.sleep(0.2)
            p.kill()
        if not output.count("test"):
            err_info = "%s device file doesn't match 'test':%s" % (char_dev, output)
            raise error.TestFail(err_info)

    def unhotplug_serial_device(hotplug_type, char_dev, index=1):
        if hotplug_type == "qmp":
            del_dev_opt = "device_del %s%s" % (char_dev, index)
            del_char_opt = "chardev-remove %s%s" % (char_dev, index)
            virsh.qemu_monitor_command(vm_name, del_dev_opt, "--hmp")
            virsh.qemu_monitor_command(vm_name, del_char_opt, "--hmp")
        elif hotplug_type == "attach":
            xmlf = "%s/xml_%s%s" % (tmp_dir, char_dev, index)
            virsh.detach_device(vm_name, xmlf, flagstr="--live")

    def confirm_unhotplug_result(char_dev, index=1):
        serial_file = "/dev/virtio-ports/%s%s" % (char_dev, index)
        result = virsh.qemu_monitor_command(vm_name, "info qtree", "--hmp")
        uh_o = result.stdout.strip()
        if uh_o.count("chardev = %s%s" % (char_dev, index)):
            raise error.TestFail("Still can get serial device info: '%s'" % uh_o)
        if not session.cmd_status("test -e %s" % serial_file):
            raise error.TestFail("File '%s' still exists after unhotplug" % serial_file)

    # run test case
    try:
        # increase workload
        if load_type in ['cpu', 'memory']:
            utils_test.load_stress("stress_in_vms", load_vms, params)
        else:
            utils_test.load_stress("iozone_in_vms", load_vms, params)

        if test_type == "multi":
            for i in range(test_count):
                if char_dev == "pty":
                    ptsid = utils_misc.aton(utils_misc.get_dev_pts_max_id()) + 1
                else:
                    ptsid = 0
                hotplug_device(hotplug_type, char_dev, i + 1, id=ptsid)
                confirm_hotplug_result(char_dev, i + 1, id=ptsid)
                unhotplug_serial_device(hotplug_type, char_dev, i + 1)
                confirm_unhotplug_result(char_dev, i + 1)
        elif test_type == "circle":
            if char_dev != "all":
                for i in range(test_count):
                    if char_dev == "pty":
                        ptsid = utils_misc.aton(utils_misc.get_dev_pts_max_id()) + 1
                    else:
                        ptsid = 0
                    #1.hotplug serial device
                    hotplug_device(hotplug_type, char_dev, id=ptsid)
                    #2.confirm hotplug result
                    confirm_hotplug_result(char_dev, id=ptsid)
                    #3.unhotplug serial device
                    unhotplug_serial_device(hotplug_type, char_dev)
                    #4.confirm unhotplug result
                    confirm_unhotplug_result(char_dev)
            else:
                for i in range(test_count):
                    #1.hotplug serial device
                    hotplug_device(hotplug_type, "file")
                    hotplug_device(hotplug_type, "socket")
                    ptsid = utils_misc.aton(utils_misc.get_dev_pts_max_id()) + 1
                    hotplug_device(hotplug_type, "pty", id=ptsid)

                    #2.confirm hotplug result
                    confirm_hotplug_result("file")
                    confirm_hotplug_result("socket")
                    confirm_hotplug_result("pty", id=ptsid)

                    #3.unhotplug serial device
                    unhotplug_serial_device(hotplug_type, "file")
                    unhotplug_serial_device(hotplug_type, "socket")
                    unhotplug_serial_device(hotplug_type, "pty")

                    #4.confirm unhotplug result
                    confirm_unhotplug_result("file")
                    confirm_unhotplug_result("socket")
                    confirm_unhotplug_result("pty")
    finally:
        session.close()
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
