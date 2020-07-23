import os
import time
import re
import logging
import signal
import aexpect
import shutil

from aexpect import ShellTimeoutError
from aexpect import ShellProcessTerminatedError

from virttest import libvirt_version
from virttest import virsh
from virttest import data_dir
from virttest import remote
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml.devices.panic import Panic

from xml.dom.minidom import parseString


def run(test, params, env):
    """
    Test command: virsh event and virsh qemu-monitor-event

    1. Run virsh event/qemu-monitor-event in a new ShellSession
    2. Trigger various events
    3. Catch the return of virsh event and qemu-monitor-event, and check it.
    """

    vms = []
    if params.get("multi_vms") == "yes":
        vms = env.get_all_vms()
    else:
        vm_name = params.get("main_vm")
        vms.append(env.get_vm(vm_name))

    event_name = params.get("event_name")
    event_all_option = "yes" == params.get("event_all_option", "no")
    event_list_option = "yes" == params.get("event_list_option", "no")
    event_loop = "yes" == params.get("event_loop", "no")
    event_timeout = params.get("event_timeout")
    event_option = params.get("event_option", "")
    status_error = "yes" == params.get("status_error", "no")
    qemu_monitor_test = "yes" == params.get("qemu_monitor_test", "no")
    signal_name = params.get("signal", None)
    panic_model = params.get("panic_model")
    addr_type = params.get("addr_type")
    addr_iobase = params.get("addr_iobase")
    event_cmd = "event"
    dump_path = '/var/lib/libvirt/qemu/dump'
    if qemu_monitor_test:
        event_cmd = "qemu-monitor-event"
    events_list = params.get("events_list")
    if events_list:
        events_list = events_list.split(",")
    else:
        events_list = []
    virsh_dargs = {'debug': True, 'ignore_status': True}
    virsh_session = aexpect.ShellSession(virsh.VIRSH_EXEC)
    for dom in vms:
        if dom.is_alive():
            dom.destroy()

    vmxml_backup = []
    for dom in vms:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(dom.name)
        vmxml_backup.append(vmxml.copy())

    tmpdir = data_dir.get_tmp_dir()
    new_disk = os.path.join(tmpdir, "%s_new_disk.img" % dom.name)

    def create_iface_xml():
        """
        Create interface xml file
        """
        iface = Interface("bridge")
        iface.source = eval("{'bridge':'virbr0'}")
        iface.model = "virtio"
        logging.debug("Create new interface xml: %s", iface)
        return iface

    def add_disk(vm_name, init_source, target_device, extra_param):
        """
        Add disk/cdrom for test vm

        :param vm_name: guest name
        :param init_source: source file
        :param target_device: target of disk device
        :param extra_param: additional arguments to command
        """
        if not os.path.exists(new_disk):
            open(new_disk, 'a').close()
        if virsh.is_alive(vm_name) and 'cdrom' in extra_param:
            virsh.destroy(vm_name)
        virsh.attach_disk(vm_name, init_source, target_device,
                          extra_param, **virsh_dargs)

    def wait_for_shutoff(vm):
        """
        Wait for the vm to reach state shutoff
        :param vm: VM instance
        """

        def is_shutoff():
            state = vm.state()
            logging.debug("Current state: %s", state)
            return "shut off" in state
        utils_misc.wait_for(is_shutoff, timeout=90, first=1,
                            step=1, text="Waiting for vm state to be shut off")

    def trigger_events(dom, events_list=[]):
        """
        Trigger various events in events_list

        :param dom: the vm objects corresponding to the domain
        :return: the expected output that virsh event command prints out
        """
        expected_events_list = []
        save_path = os.path.join(tmpdir, "%s_event.save" % dom.name)
        print(dom.name)
        xmlfile = dom.backup_xml()

        try:
            for event in events_list:
                logging.debug("Current event is: %s", event)
                if event in ['start', 'restore', 'create', 'edit', 'define', 'undefine', 'crash']:
                    if dom.is_alive():
                        dom.destroy()
                        if event in ['create', 'define']:
                            dom.undefine()
                else:
                    if not dom.is_alive():
                        dom.start()
                        dom.wait_for_login().close()
                        if event == "resume":
                            dom.pause()

                if event == "undefine":
                    virsh.undefine(dom.name, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Undefined Removed")
                elif event == "create":
                    virsh.create(xmlfile, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Resumed Unpaused")
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Started Booted")
                elif event == "destroy":
                    virsh.destroy(dom.name, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Stopped Destroyed")
                elif event == "define":
                    virsh.define(xmlfile, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Defined Added")
                elif event == "start":
                    virsh.start(dom.name, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Resumed Unpaused")
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Started Booted")
                    dom.wait_for_login().close()
                elif event == "suspend":
                    virsh.suspend(dom.name, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Suspended Paused")
                    if not libvirt_version.version_compare(5, 3, 0):
                        expected_events_list.append("'lifecycle' for %s:"
                                                    " Suspended Paused")
                elif event == "resume":
                    virsh.resume(dom.name, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Resumed Unpaused")
                elif event == "save":
                    virsh.save(dom.name, save_path, **virsh_dargs)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Suspended Paused")
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Stopped Saved")
                elif event == "restore":
                    if not os.path.exists(save_path):
                        logging.error("%s not exist", save_path)
                    else:
                        virsh.restore(save_path, **virsh_dargs)
                        expected_events_list.append("'lifecycle' for %s:"
                                                    " Started Restored")
                        expected_events_list.append("'lifecycle' for %s:"
                                                    " Resumed Snapshot")
                elif event == "edit":
                    #Check whether 'description' element exists.
                    domxml = virsh.dumpxml(dom.name).stdout.strip()
                    find_desc = parseString(domxml).getElementsByTagName("description")
                    if find_desc == []:
                        #If not exists, add one for it.
                        logging.info("Adding <description> to guest")
                        virsh.desc(dom.name, "--config", "Added desc for testvm", **virsh_dargs)
                    #The edit operation is to delete 'description' element.
                    edit_cmd = [r":g/<description.*<\/description>/d"]
                    utlv.exec_virsh_edit(dom.name, edit_cmd)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Defined Updated")
                elif event == "shutdown":
                    if signal_name is None:
                        virsh.shutdown(dom.name, **virsh_dargs)
                        # Wait a few seconds for shutdown finish
                        time.sleep(3)
                        if utils_misc.compare_qemu_version(2, 9, 0):
                            #Shutdown reason distinguished from qemu_2.9.0-9
                            expected_events_list.append("'lifecycle' for %s:"
                                                        " Shutdown Finished after guest request")
                    else:
                        os.kill(dom.get_pid(), getattr(signal, signal_name))
                        if utils_misc.compare_qemu_version(2, 9, 0):
                            expected_events_list.append("'lifecycle' for %s:"
                                                        " Shutdown Finished after host request")
                    if not utils_misc.compare_qemu_version(2, 9, 0):
                        expected_events_list.append("'lifecycle' for %s:"
                                                    " Shutdown Finished")
                    wait_for_shutoff(dom)
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Stopped Shutdown")
                elif event == "crash":
                    if not vmxml.xmltreefile.find('devices').findall('panic'):
                        # Set panic device
                        panic_dev = Panic()
                        panic_dev.model = panic_model
                        panic_dev.addr_type = addr_type
                        panic_dev.addr_iobase = addr_iobase
                        vmxml.add_device(panic_dev)
                    vmxml.on_crash = "coredump-restart"
                    vmxml.sync()
                    logging.info("Guest xml now is: %s", vmxml)
                    dom.start()
                    session = dom.wait_for_login()
                    # Stop kdump in the guest
                    session.cmd("systemctl stop kdump", ignore_all_errors=True)
                    # Enable sysRq
                    session.cmd("echo 1 > /proc/sys/kernel/sysrq")
                    try:
                        # Crash the guest
                        session.cmd("echo c > /proc/sysrq-trigger", timeout=60)
                    except (ShellTimeoutError, ShellProcessTerminatedError) as details:
                        logging.info(details)
                    session.close()
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Crashed Panicked")
                    expected_events_list.append("'lifecycle' for %s:"
                                                " Resumed Unpaused")
                elif event == "reset":
                    virsh.reset(dom.name, **virsh_dargs)
                    expected_events_list.append("'reboot' for %s")
                elif event == "vcpupin":
                    virsh.vcpupin(dom.name, '0', '0', **virsh_dargs)
                    expected_events_list.append("'tunable' for %s:"
                                                "\n\tcputune.vcpupin0: 0")
                elif event == "emulatorpin":
                    virsh.emulatorpin(dom.name, '0', **virsh_dargs)
                    expected_events_list.append("'tunable' for %s:"
                                                "\n\tcputune.emulatorpin: 0")
                elif event == "setmem":
                    mem_size = int(params.get("mem_size", 512000))
                    virsh.setmem(dom.name, mem_size, **virsh_dargs)
                    expected_events_list.append("'balloon-change' for %s:")
                elif event == "device-added-removed":
                    add_disk(dom.name, new_disk, 'vdb', '')
                    expected_events_list.append("'device-added' for %s:"
                                                " virtio-disk1")
                    virsh.detach_disk(dom.name, 'vdb', **virsh_dargs)
                    expected_events_list.append("'device-removed' for %s:"
                                                " virtio-disk1")
                    ifaces = vmxml.devices.by_device_tag('interface')
                    if ifaces:
                        iface_xml_obj = ifaces[0]
                        iface_xml_obj.del_address()
                        logging.debug(iface_xml_obj)
                    else:
                        test.error('No interface in vm to be detached.')

                    virsh.detach_device(dom.name, iface_xml_obj.xml,
                                        wait_remove_event=True, event_timeout=60,
                                        **virsh_dargs)
                    expected_events_list.append("'device-removed' for %s:"
                                                " net0")
                    virsh.attach_device(dom.name, iface_xml_obj.xml, **virsh_dargs)
                    expected_events_list.append("'device-added' for %s:"
                                                " net0")
                elif event == "change-media":
                    target_device = "hdc"
                    device_target_bus = params.get("device_target_bus", "ide")
                    disk_blk = vm_xml.VMXML.get_disk_blk(dom.name)
                    logging.info("disk_blk %s", disk_blk)
                    if target_device not in disk_blk:
                        logging.info("Adding cdrom to guest")
                        if dom.is_alive():
                            dom.destroy()
                        add_disk(dom.name, "''", target_device,
                                 ("--type cdrom --sourcetype file --driver qemu " +
                                  "--config --targetbus %s" % device_target_bus))
                        dom.start()
                    all_options = new_disk + " --insert"
                    virsh.change_media(dom.name, target_device,
                                       all_options, **virsh_dargs)
                    expected_events_list.append("'tray-change' for %s disk" + " .*%s.*:" % device_target_bus +
                                                " opened")
                    expected_events_list.append("'tray-change' for %s disk" + " .*%s.*:" % device_target_bus +
                                                " closed")
                    all_options = new_disk + " --eject"
                    virsh.change_media(dom.name, target_device,
                                       all_options, **virsh_dargs)
                    expected_events_list.append("'tray-change' for %s disk" + " .*%s.*:" % device_target_bus +
                                                " opened")
                elif event == "hwclock":
                    session = dom.wait_for_login()
                    try:
                        session.cmd("hwclock --systohc", timeout=60)
                    except (ShellTimeoutError, ShellProcessTerminatedError) as details:
                        logging.info(details)
                    session.close()
                    expected_events_list.append("'rtc-change' for %s:")
                elif event == "metadata_set":
                    metadata_uri = params.get("metadata_uri")
                    metadata_key = params.get("metadata_key")
                    metadata_value = params.get("metadata_value")
                    virsh.metadata(dom.name,
                                   metadata_uri,
                                   options="",
                                   key=metadata_key,
                                   new_metadata=metadata_value,
                                   **virsh_dargs)
                    expected_events_list.append("'metadata-change' for %s: "
                                                "element http://app.org/")
                elif event == "metadata_edit":
                    metadata_uri = "http://herp.derp/"
                    metadata_key = "herp"
                    metadata_value = "<derp xmlns:foobar='http://foo.bar/'>foo<bar></bar></derp>"
                    virsh_cmd = r"virsh metadata %s --uri %s --key %s %s"
                    virsh_cmd = virsh_cmd % (dom.name, metadata_uri,
                                             metadata_key, "--edit")
                    session = aexpect.ShellSession("sudo -s")
                    logging.info("Running command: %s", virsh_cmd)
                    try:
                        session.sendline(virsh_cmd)
                        session.sendline(r":insert")
                        session.sendline(metadata_value)
                        session.sendline(".")
                        session.send('ZZ')
                        remote.handle_prompts(session, None, None, r"[\#\$]\s*$",
                                              debug=True, timeout=60)
                    except Exception as e:
                        test.error("Error occured: %s" % e)
                    session.close()
                    # Check metadata after edit
                    virsh.metadata(dom.name,
                                   metadata_uri,
                                   options="",
                                   key=metadata_key,
                                   **virsh_dargs)
                    expected_events_list.append("'metadata-change' for %s: "
                                                "element http://app.org/")
                elif event == "metadata_remove":
                    virsh.metadata(dom.name,
                                   metadata_uri,
                                   options="--remove",
                                   key=metadata_key,
                                   **virsh_dargs)
                    expected_events_list.append("'metadata-change' for %s: "
                                                "element http://app.org/")
                else:
                    test.error("Unsupported event: %s" % event)
                # Event may not received immediately
                time.sleep(3)
        finally:
            if os.path.exists(save_path):
                os.unlink(save_path)
            if os.path.exists(new_disk):
                os.unlink(new_disk)
        return [(dom.name, event) for event in expected_events_list]

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
        :param expected_events_list: A list of expected events
        """
        logging.debug("Actual events: %s", output)
        event_idx = 0
        for dom_name, event in expected_events_list:
            if event in expected_events_list[0]:
                event_idx = 0
            event_str = "event " + event % ("domain %s" % dom_name)
            logging.info("Expected event: %s", event_str)
            match = re.search(event_str, output[event_idx:])
            if match:
                event_idx = event_idx + match.start(0) + len(match.group(0))
                continue
            else:
                test.fail("Not find expected event:%s. Is your "
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
            event_cmd += " %s" % event_option
            if event_name and not qemu_monitor_test:
                event_cmd += " --event %s" % event_name
            if event_timeout:
                event_cmd += " --timeout %s" % event_timeout
            # Run the command in a new virsh session, then waiting for
            # various events
            logging.info("Sending '%s' to virsh shell", event_cmd)
            virsh_session.sendline(event_cmd)
        elif qemu_monitor_test:
            result = virsh.qemu_monitor_event(event=event_name,
                                              event_timeout=event_timeout,
                                              options=event_option,
                                              **virsh_dargs)
            utlv.check_exit_status(result, status_error)
        else:
            result = virsh.event(event=event_name,
                                 event_timeout=event_timeout,
                                 options=event_option, **virsh_dargs)
            utlv.check_exit_status(result, status_error)

        if not status_error:
            if not event_list_option:
                expected_events_list = []
                virsh_dargs['ignore_status'] = False
                for dom in vms:
                    expected_events_list.extend(trigger_events(dom, events_list))
                if event_timeout:
                    # Make sure net-event will timeout on time
                    time.sleep(int(event_timeout))
                elif event_loop:
                    virsh_session.send_ctrl("^C")
                    time.sleep(5)
                ret_output = virsh_session.get_stripped_output()
                if qemu_monitor_test:
                    # Not check for qemu-monitor-event output
                    expected_events_list = []
                check_output(ret_output, expected_events_list)
    finally:
        for dom in vms:
            if dom.is_alive():
                dom.destroy()
        virsh_session.close()
        for xml in vmxml_backup:
            xml.sync()
        if os.path.exists(dump_path):
            shutil.rmtree(dump_path)
            os.mkdir(dump_path)
