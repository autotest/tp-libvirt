# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Nannan Li <nanli@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os
import re
import subprocess

from avocado.utils import process

from virttest import virsh
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import disk
from virttest.utils_test import libvirt

virsh_dargs = {"ignore_status": False, "shell": True}


def run(test, params, env):
    """
    Make disk produce I/O error due to "message", then check the error
    in "virsh event", vm log and "virsh dominfo"

    Note: "message" is a kind of I/O error reason. It means that the hypervisor
    reported a string description of the I/O error. The errors are usually logged
    into the domain log file or the last instance of the error string can be
    queried via virDomainGetMessages().
    """

    def setup_test():
        """
        Prepare one error device mapper。
        """
        test.log.info("Setup env.")
        process.run(prepare_file, **virsh_dargs)
        # Find a free loop device
        global free_loop_dev
        free_loop_dev = process.run("losetup --find", **virsh_dargs).stdout_text.strip()
        # Setup a loop device and create error device mapper
        process.run('losetup %s %s' % (free_loop_dev, device_manager_path), **virsh_dargs)
        dm_table = """0 261144 linear %s 0
                    261144 5 error
                    261149 787427 linear %s 261139""" % (free_loop_dev, free_loop_dev)
        try:
            subprocess.run(["sudo", "dmsetup", "create", device_manager],
                           input=dm_table.encode('utf-8'), stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as e:
            test.log.debug("create device manager failed :%s", e.stderr.decode('utf-8'))

    def run_test():
        """
        Attach disk and check error message.
        """
        test.log.info("TEST_STEP1: Attach a new disk")
        vm.start()
        session = vm.wait_for_login()
        new_disk = disk.Disk()
        new_disk.setup_attrs(**disk_dict)
        virsh.attach_device(vm_name, new_disk.xml, flagstr="--current",
                            wait_for_event=True, event_timeout=100, **virsh_dargs)

        test.log.info("TEST_STEP2: Write file in guest and check I/O error message.")
        virsh_session = virsh.EventTracker.start_get_event(vm_name)
        try:
            output = session.cmd(dd_in_guest)
        except Exception as e:
            if dd_msg not in str(e):
                test.fail("Write data in guest should produce error: %s in %s" % (dd_msg, output))
        else:
            test.fail("Except error: in %s" % output)

        test.log.info("TEST_STEP3: Check event output.")
        event_output = virsh.EventTracker.finish_get_event(virsh_session)
        if not re.search(event_msg, event_output):
            test.fail('Not find: %s from event output:%s' % (event_msg, event_output))

        test.log.info("TEST_STEP4: Check about I/O error in vm log.")
        qemu_log = os.path.join('/var/log/libvirt/qemu/', "%s.log" % vm_name)
        if not libvirt.check_logfile(guest_log_msg, qemu_log, str_in_log=True):
            test.fail('Find unexpected error:%s in log file:%s' % (guest_log_msg, qemu_log))

        test.log.info("TEST_STEP5: Check about I/O error in virsh domain info.")
        dominfo = virsh.dominfo(vm_name, debug=True)
        libvirt.check_result(dominfo, expected_match=dominfo_msg)

    def teardown_test():
        """
        Clean data.
        """
        test.log.info("TEST_TEARDOWN: Clean up env.")
        if session:
            session.close()
        bkxml.sync()
        process.run('sudo losetup -d %s' % free_loop_dev, **virsh_dargs)
        process.run('sudo dmsetup remove %s' % device_manager, **virsh_dargs)
        if os.path.exists(device_manager_path):
            os.remove(device_manager_path)

    libvirt_version.is_libvirt_feature_supported(params)

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    prepare_file = params.get("prepare_file")
    device_manager = params.get("device_manager")
    device_manager_path = params.get("device_manager_path")
    disk_dict = eval(params.get("disk_dict", "{}"))
    dd_in_guest = params.get("dd_in_guest")
    dd_msg = params.get("dd_msg")
    event_msg = params.get("event_msg")
    guest_log_msg = params.get("guest_log_msg")
    dominfo_msg = params.get("dominfo_msg")
    session = None

    try:
        setup_test()
        run_test()

    finally:
        teardown_test()
