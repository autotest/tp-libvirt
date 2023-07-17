# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import time

import aexpect
import os
import platform
import re
import stat
import uuid

from avocado.utils import process

from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.chardev import check_points
from provider.chardev import chardev_base


def run(test, params, env):
    """
    Test chardev data transfer function
    Scenarios:
    1) chardev: console, serial, channel .
    2) source type: file, pipe
    """

    def setup_test():
        """
        Guest setup:
            Add below line in VM kernel command line: console=ttyS0,115200
        Host setups:
            pipe: create 2 pipes use mkfifo command with name XXX.in and XXX.out
            file: create a empty file (in somewhere other than /root)
        """
        test.log.info("Setup env: Set guest kernel command line.")
        if chardev != "channel":
            if not vm.set_kernel_console(
                    device, speed, remove=False, guest_arch_name=machine):
                test.fail("Config kernel for console failed.")

        test.log.info("Setup env: Create file on host")
        params.update({'source_path': set_host_file(chardev_type)})

        test.log.info("Setup env: Add chardev device with type '{}' ".format(chardev))
        add_chardev(params.get('source_path'))

    def run_test():
        """
        Check data transfer, device alias in vm dumpxml and audit log
        """
        test.log.info("TEST_STEP1: Start guest")
        vm.start()
        original_session, check_file = get_check_file()

        test.log.info("TEST_STEP2: Check alias exist in guest xml")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt_vmxml.check_guest_xml_by_xpaths(
            vmxml, [{'element_attrs': ['.//alias[@name="%s"]' % device_alias]}])

        test.log.info("TEST_STEP3: Check audit log exist chardev")
        check_points.check_audit_log(test, eval(audit_log_msg % params.get('source_path')))

        test.log.info("TEST_STEP4: Check booting info")
        check_boot_info(original_session, check_file)

        test.log.info("TEST_STEP5: Send message to the chardev from guest")
        check_message_from_guest(check_file)

        if chardev_type == "pipe":
            test.log.info("TEST_STEP6: Send message from host to guest")
            check_message_from_host()

    def teardown_test():
        """
        Clean data.
        """
        if chardev != "channel":
            vm.set_kernel_console(device, speed, remove=True,
                                  guest_arch_name=machine)
        bkxml.sync()
        if chardev_type == "pipe":
            clean_pipe_file(pipe_in)
            clean_pipe_file(pipe_out)
        elif chardev_type == "file":
            clean_pipe_file(file_path)
        clean_pipe_file(out_file)

        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        session.cmd("rm -f %s" % guest_out)
        session.close()

    def clean_pipe_file(file):
        """
        Clean pipe file

        :params: file: file path
        """
        if os.path.exists(file):
            os.remove(file)

    def get_check_file():
        """
        Get check file

        :return: original_session, check_file: session, file path
        """
        check_file = ''
        if chardev_type == "file":
            check_file = params.get('source_path')
            cmd = "tail -f %s > %s " % (check_file, out_file)
        elif chardev_type == "pipe":
            check_file = pipe_out
            cmd = "cat < %s > %s " % (check_file, out_file)
        original_session = aexpect.ShellSession(cmd, auto_close=False)
        test.log.debug('Execute "%s" before start vm to get boot info', cmd)
        vm.wait_for_login(timeout=240).close()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.debug("After start vm, get vmxml: \n%s", vmxml)

        return original_session, check_file

    def get_chardev_port(chardev):
        """
        Get current port value of new added chardev

        :params: chardev , chardev type, such as console, serial ,channel
        :return: port: the port of new added chardev
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        dev = vmxml.xmltreefile.find('devices').findall(chardev)
        port = None
        if chardev == "channel":
            return target_name
        for item in dev:
            if item.find('alias').get('name') == device_alias:
                port = item.find('target').get('port')
                test.log.debug("Get port='%s' from chardev", port)
                return port
        if port is None:
            test.fail('Alias does not exist in %s', chardev)

    def add_chardev(source_path):
        """
        Add chardev

        :params: source path: the source path of new chardev
        """
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.remove_all_device_by_type("console")
        vmxml.remove_all_device_by_type("serial")
        vmxml.remove_all_device_by_type("channel")
        dest_dict = eval(
            device_dict % (source_path, target_type, device_alias))
        test.log.debug("Add chardev dict is: %s", dest_dict)
        libvirt_vmxml.modify_vm_device(
            vmxml=vmxml, dev_type=chardev, dev_dict=dest_dict)

    def set_host_file(chardev_type):
        """
        Set the file according to the chardev type

        :params: chardev_type: chardev type, file or pipe
        """
        source_path = ""
        if chardev_type == "file":
            process.run("touch %s " % file_path, shell=True)
            os.chmod(file_path, stat.S_IWUSR | stat.S_IEXEC)
            source_path = file_path

        elif chardev_type == "pipe":
            clean_pipe_file(pipe_in)
            os.mkfifo(pipe_in)

            clean_pipe_file(pipe_out)
            os.mkfifo(pipe_out)

            source_path = pipe_path

        return source_path

    def check_message_from_guest(check_file):
        """
        Check message sent from gust

        :params: check_file: the check file path
        """
        chardev_port = get_chardev_port(chardev)
        host_session = aexpect.ShellSession(host_cmd % check_file,
                                            auto_close=False)
        test.log.debug('Execute cmd is:%s ', host_cmd % check_file)
        chardev_base.send_message(vm, "guest", send_msg=guest_msg,
                                  send_path=target_path + chardev_port)
        output = host_session.get_output()
        host_session.close()
        if guest_msg not in output:
            test.fail("Not get '%s' in '%s'" % (guest_msg, output))
        else:
            test.log.debug("Check %s exist on host", guest_msg)

    def check_message_from_host():
        """
        Check the message got from host
        """
        chardev_port = get_chardev_port(chardev)

        vm_session = vm.wait_for_login(timeout=240)
        cmd = "cat %s > %s &" % (target_path + chardev_port, guest_out)
        test.log.debug("Execute cmd:'%s' on guest ", cmd)
        vm_session.sendline(cmd)
        time.sleep(5)
        chardev_base.send_message(vm, "host", send_msg=host_msg,
                                  send_path=pipe_in)

        vm_session_new = vm.wait_for_login(timeout=240)
        status, output = vm_session_new.cmd_status_output(
            "cat %s | grep '%s' " % (guest_out, host_msg))
        vm_session_new.close()
        vm_session.close()

        if not re.search(host_msg, output):
            test.fail("Not get %s in '%s' " % (host_msg, output))
        else:
            test.log.debug("Check '%s' exist in :%s" % (host_msg, output))

    def check_boot_info(session, file):
        """
        Check guest boot info

        :params: session: guest session
        :params: file: check file
        """
        if chardev != "channel":
            for item in boot_prompt:
                if not utils_misc.wait_for(
                        lambda: chardev_base.get_match_count(
                            test, out_file, item) >= 1, 80):
                    test.fail("Not get %s in '%s' " % (item, file))
        session.close()

    vm_name = params.get("main_vm")
    machine = platform.machine()

    device = params.get('device')
    speed = params.get('speed')
    chardev = params.get('chardev')
    chardev_type = params.get('chardev_type')
    target_type = params.get('target_type')
    pipe_in = params.get('pipe_in')
    pipe_out = params.get('pipe_out')
    file_path = params.get('file_path')
    target_name = params.get("target_name", "")
    target_path = params.get("target_path")
    pipe_path = params.get("pipe_path")

    device_dict = params.get('device_dict', '{}')
    boot_prompt = eval(params.get('boot_prompt'))
    audit_log_msg = params.get('audit_log_msg')
    host_cmd = params.get('host_cmd')
    host_msg = params.get("host_msg")
    guest_msg = params.get("guest_msg")

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    device_alias = "ua-" + str(uuid.uuid4())
    out_file = "/tmp/output_content.txt"
    guest_out = "/tmp/guest_out.txt"

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
