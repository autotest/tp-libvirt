import os
import time
import logging

import aexpect

from autotest.client.shared import error

from virttest import virsh
from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv


def run(test, params, env):
    """
    Test command: virsh event and virsh qemu-monitor-event

    1. Run virsh event/qemu-monitor-event in a new ShellSession
    2. Trigger various events
    3. Catch the return of virsh event and qemu-monitor-event, and check it.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    event_name = params.get("event_name")
    event_all_option = "yes" == params.get("event_all_option", "no")
    event_list_option = "yes" == params.get("event_list_option", "no")
    event_loop = "yes" == params.get("event_loop", "no")
    event_timeout = params.get("event_timeout")
    event_option = params.get("event_option", "")
    status_error = "yes" == params.get("status_error", "no")
    qemu_monitor_test = "yes" == params.get("qemu_monitor_test", "no")
    event_cmd = "event"
    if qemu_monitor_test:
        event_cmd = "qemu-monitor-event"
    events_list = params.get("events_list")
    if events_list:
        events_list = events_list.split(",")
    else:
        events_list = []
    virsh_dargs = {'debug': True, 'ignore_status': True}
    virsh_session = aexpect.ShellSession(virsh.VIRSH_EXEC)
    if vm.is_alive():
        vm.destroy()
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    vmxml_backup = vmxml.copy()

    def trigger_events(events_list=[]):
        """
        Trigger various events in events_list
        """
        expected_events_list = []
        tmpdir = data_dir.get_tmp_dir()
        save_path = os.path.join(tmpdir, "vm_event.save")
        new_disk = os.path.join(tmpdir, "new_disk.img")
        try:
            for event in events_list:
                if event in ['start', 'restore']:
                    if vm.is_alive():
                        vm.destroy()
                else:
                    if not vm.is_alive():
                        vm.start()
                        vm.wait_for_login().close()
                if event == "start":
                    virsh.start(vm_name, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Started Booted")
                    vm.wait_for_login().close()
                elif event == "save":
                    virsh.save(vm_name, save_path, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Stopped Saved")
                elif event == "restore":
                    if not os.path.exists(save_path):
                        logging.error("%s not exist", save_path)
                    else:
                        virsh.restore(save_path, **virsh_dargs)
                        expected_events_list.append("'lifecycle' for %s:"
                                                    " Started Restored")
                elif event == "destroy":
                    virsh.destroy(vm_name, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Stopped Destroyed")
                elif event == "reset":
                    virsh.reset(vm_name, **virsh_dargs)
                    expected_events_list.append("'reboot' for %s")
                elif event == "vcpupin":
                    virsh.vcpupin(vm_name, '0', '0', **virsh_dargs)
                    expected_events_list.append("'tunable' for %s:"
                                                "\n\tcputune.vcpupin0: 0")
                elif event == "emulatorpin":
                    virsh.emulatorpin(vm_name, '0', **virsh_dargs)
                    expected_events_list.append("'tunable' for %s:"
                                                "\n\tcputune.emulatorpin: 0")
                elif event == "setmem":
                    mem_size = int(params.get("mem_size", 512000))
                    virsh.setmem(vm_name, mem_size, **virsh_dargs)
                    expected_events_list.append("'balloon-change' for %s:")
                elif event == "detach-disk":
                    if not os.path.exists(new_disk):
                        open(new_disk, 'a').close()
                    # Attach disk firstly, this event will not be catched
                    virsh.attach_disk(vm_name, new_disk, 'vdb', **virsh_dargs)
                    virsh.detach_disk(vm_name, 'vdb', **virsh_dargs)
                    expected_events_list.append("'device-removed' for %s:"
                                                " virtio-disk1")
                else:
                    raise error.TestError("Unsupported event: %s" % event)
                # Event may not received immediately
                time.sleep(3)
        finally:
            if os.path.exists(save_path):
                os.unlink(save_path)
            if os.path.exists(new_disk):
                os.unlink(new_disk)
            return expected_events_list

    def check_output(output, expected_events_list):
        """
        Check received domain event in output.

        :param output: The virsh shell output, such as:
            Welcome to virsh, the virtualization interactive terminal.

            Type:  'help' for help with commands
                   'quit' to quit

            virsh # event 'lifecycle' for domain avocado-vt-vm1: Started Booted
            events received: 1

            virsh #
         : expected_events_list: A list of expected events
        """
        logging.debug("Actual events: %s", output)
        for event in expected_events_list:
            event_str = "event " + event % ("domain " + vm_name)
            logging.debug("Expected event: %s", event_str)
            if event_str in output:
                continue
            else:
                raise error.TestFail("Not find expected event:%s. Is your "
                                     "guest too slow to get started in %ss?" %
                                     (event_str, event_timeout))

    try:
        # Set vcpu placement to static to avoid emulatorpin fail
        vmxml.placement = 'static'
        # Using a large memeoy(>1048576) to avoid setmem fail
        vmxml.max_mem = 2097152
        vmxml.current_mem = 2097152
        vmxml.sync()
        if event_all_option and not qemu_monitor_test:
            event_option += " --all"
        if event_list_option:
            event_option += " --list"
        if event_loop:
            event_option += " --loop"

        if not status_error and not event_list_option:
            event_cmd += " --domain %s %s" % (vm_name, event_option)
            if event_name and not qemu_monitor_test:
                event_cmd += " --event %s" % event_name
            if event_timeout:
                event_cmd += " --timeout %s" % event_timeout
                if not status_error:
                    event_timeout = int(event_timeout)
            # Run the command in a new virsh session, then waiting for
            # various events
            logging.info("Sending '%s' to virsh shell", event_cmd)
            virsh_session.sendline(event_cmd)
        elif qemu_monitor_test:
            result = virsh.qemu_monitor_event(domain=vm_name, event=event_name,
                                              event_timeout=event_timeout,
                                              options=event_option, **virsh_dargs)
            utlv.check_exit_status(result, status_error)
        else:
            result = virsh.event(domain=vm_name, event=event_name,
                                 event_timeout=event_timeout,
                                 options=event_option, **virsh_dargs)
            utlv.check_exit_status(result, status_error)

        if not status_error:
            if not event_list_option:
                expected_events_list = trigger_events(events_list)
                if event_timeout:
                    # Make sure net-event will timeout on time
                    time.sleep(event_timeout)
                elif event_loop:
                    virsh_session.send_ctrl("^C")
                ret_output = virsh_session.get_stripped_output()
                if qemu_monitor_test:
                    # Not check for qemu-monitor-event output
                    expected_events_list = []
                check_output(ret_output, expected_events_list)
    finally:
        virsh_session.close()
        if vm.is_alive():
            vm.destroy()
        vmxml_backup.sync()
