import os
import time
import re
import logging
import signal
import aexpect
import shutil

from avocado.utils import process
from aexpect import ShellTimeoutError
from aexpect import ShellProcessTerminatedError

from virttest import libvirt_version
from virttest import virsh
from virttest import data_dir
from virttest import remote
from virttest import utils_misc
from virttest import utils_hotplug
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml.devices.panic import Panic
from virttest.libvirt_xml.devices.watchdog import Watchdog

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
    disk_format = params.get("disk_format", "")
    disk_prealloc = "yes" == params.get("disk_prealloc", "yes")
    event_cmd = "event"
    dump_path = '/var/lib/libvirt/qemu/dump'
    part_format = params.get("part_format")
    strict_order = "yes" == params.get("strict_order", "no")
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
    mount_point = tmpdir
    small_part = os.path.join(tmpdir, params.get("part_name", "io-error_part"))

    def create_iface_xml():
        """
        Create interface xml file
        """
        iface = Interface("bridge")
        iface.source = eval("{'bridge':'virbr0'}")
        iface.model = "virtio"
        logging.debug("Create new interface xml: %s", iface)
        return iface

    def add_disk(vm_name, init_source, target_device, extra_param, format=''):
        """
        Add disk/cdrom for test vm

        :param vm_name: guest name
        :param init_source: source file
        :param target_device: target of disk device
        :param extra_param: additional arguments to command
        :param format: init_source format(qcow2 or raw)
        """
        if not os.path.exists(init_source):
            disk_size = params.get("disk_size", "1G")
            if format == "qcow2":
                create_option = "" if not disk_prealloc else "-o preallocation=full"
                process.run('qemu-img create -f qcow2 %s %s %s' % (init_source, disk_size, create_option),
                            shell=True, verbose=True)
            elif format == "raw":
                process.run('qemu-img create -f raw %s %s' % (init_source, disk_size),
                            shell=True, verbose=True)
            else:
                open(init_source, 'a').close()
        if virsh.is_alive(vm_name) and 'cdrom' in extra_param:
            virsh.destroy(vm_name)
        if 'cdrom' in extra_param:
            init_source = "''"
        virsh.attach_disk(vm_name, init_source, target_device,
                          extra_param, **virsh_dargs)
        vmxml_disk = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug("Current vmxml after adding disk is %s\n" % vmxml_disk)

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

    def prepare_vmxml_mem(vmxml):
        """
        Prepare memory and numa settings in vmxml before hotplug dimm

        param vmxml: guest current xml
        """
        # Prepare memory settings
        vmxml.max_mem_rt = int(params.get("max_mem"))
        vmxml.max_mem_rt_slots = int(params.get("maxmem_slots"))
        mem_unit = params.get("mem_unit")
        vmxml.max_mem_rt_unit = mem_unit
        current_mem = int(params.get("current_mem"))
        vmxml.current_mem = current_mem
        vmxml.current_mem_unit = mem_unit
        vmxml.memory = int(params.get("memory"))
        # Prepare numa settings in <cpu>
        host_numa_node = utils_misc.NumaInfo()
        host_numa_node_list = host_numa_node.online_nodes
        numa_nodes = len(host_numa_node_list)
        if numa_nodes == 0:
            test.cancel("No host numa node available")
        numa_dict = {}
        numa_dict_list = []
        cpu_idx = 0
        for index in range(numa_nodes):
            numa_dict['id'] = str(index)
            numa_dict['memory'] = str(current_mem // numa_nodes)
            numa_dict['unit'] = mem_unit
            numa_dict['cpus'] = "%s-%s" % (str(cpu_idx), str(cpu_idx + 1))
            cpu_idx += 2
            numa_dict_list.append(numa_dict)
            numa_dict = {}
        vmxml.vcpu = numa_nodes * 2
        vmxml_cpu = vm_xml.VMCPUXML()
        vmxml_cpu.xml = "<cpu><numa/></cpu>"
        vmxml_cpu.numa_cell = vmxml_cpu.dicts_to_cells(numa_dict_list)
        logging.debug(vmxml_cpu.numa_cell)
        vmxml.cpu = vmxml_cpu
        vmxml.sync()

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
        new_disk = os.path.join(tmpdir, "%s_new_disk.img" % dom.name)
        dest_path = os.path.join(data_dir.get_data_dir(), "copy")

        try:
            for event in events_list:
                logging.debug("Current event is: %s", event)
                if event in ['start', 'restore', 'create', 'edit', 'define',
                             'undefine', 'crash', 'device-removal-failed',
                             'watchdog', 'io-error']:
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
                        session.cmd("echo c > /proc/sysrq-trigger", timeout=90)
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
                    iface_xml_obj = create_iface_xml()
                    iface_xml_obj.xmltreefile.write()
                    virsh.detach_device(dom.name, iface_xml_obj.xml, **virsh_dargs)
                    expected_events_list.append("'device-removed' for %s:"
                                                " net0")
                    time.sleep(2)
                    virsh.attach_device(dom.name, iface_xml_obj.xml, **virsh_dargs)
                    expected_events_list.append("'device-added' for %s:"
                                                " net0")
                elif event == "block-threshold":
                    add_disk(dom.name, new_disk, 'vdb', '', format=disk_format)
                    logging.debug(process.run('qemu-img info %s -U' % new_disk))
                    virsh.domblkthreshold(vm_name, 'vdb', '100M')
                    session = dom.wait_for_login()
                    session.cmd("mkfs.ext4 /dev/vdb && mount /dev/vdb /mnt && ls /mnt && "
                                "dd if=/dev/urandom of=/mnt/bigfile bs=1M count=300 && sync")
                    time.sleep(5)
                    session.close()
                    expected_events_list.append("'block-threshold' for %s:"
                                                " dev: vdb(%s)  104857600 29368320")
                    virsh.detach_disk(dom.name, 'vdb', **virsh_dargs)
                elif event == "change-media":
                    target_device = "hdc"
                    device_target_bus = params.get("device_target_bus", "ide")
                    disk_blk = vm_xml.VMXML.get_disk_blk(dom.name)
                    logging.info("disk_blk %s", disk_blk)
                    if target_device not in disk_blk:
                        logging.info("Adding cdrom to guest")
                        if dom.is_alive():
                            dom.destroy()
                        add_disk(dom.name, new_disk, target_device,
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
                elif event == "blockcommit":
                    disk_path = dom.get_blk_devices()['vda']['source']
                    virsh.snapshot_create_as(dom.name, "s1 --disk-only --no-metadata", **virsh_dargs)
                    snapshot_path = dom.get_blk_devices()['vda']['source']
                    virsh.blockcommit(dom.name, "vda", "--active --pivot", **virsh_dargs)
                    expected_events_list.append("'block-job' for %s: "
                                                "Active Block Commit for " + "%s" % snapshot_path + " ready")
                    expected_events_list.append("'block-job-2' for %s: "
                                                "Active Block Commit for vda ready")
                    expected_events_list.append("'block-job' for %s: "
                                                "Active Block Commit for " + "%s" % disk_path + " completed")
                    expected_events_list.append("'block-job-2' for %s: "
                                                "Active Block Commit for vda completed")
                    os.unlink(snapshot_path)
                elif event == "blockcopy":
                    disk_path = dom.get_blk_devices()['vda']['source']
                    dom.undefine()
                    virsh.blockcopy(dom.name, "vda", dest_path, "--pivot", **virsh_dargs)
                    expected_events_list.append("'block-job' for %s: "
                                                "Block Copy for " + "%s" % disk_path + " ready")
                    expected_events_list.append("'block-job-2' for %s: "
                                                "Block Copy for vda ready")
                    expected_events_list.append("'block-job' for %s: "
                                                "Block Copy for " + "%s" % dest_path + " completed")
                    expected_events_list.append("'block-job-2' for %s: "
                                                "Block Copy for vda completed")
                elif event == "detach-dimm":
                    prepare_vmxml_mem(vmxml)
                    tg_size = params.get("dimm_size")
                    tg_sizeunit = params.get("dimm_unit")
                    dimm_xml = utils_hotplug.create_mem_xml(tg_size, None, None, tg_sizeunit)
                    virsh.attach_device(dom.name, dimm_xml.xml,
                                        flagstr="--config", **virsh_dargs)
                    vmxml_dimm = vm_xml.VMXML.new_from_dumpxml(dom.name)
                    logging.debug("Current vmxml with plugged dimm dev is %s\n" % vmxml_dimm)
                    virsh.start(dom.name, **virsh_dargs)
                    dom.wait_for_login().close()
                    result = virsh.detach_device(dom.name, dimm_xml.xml, debug=True, ignore_status=True)
                    expected_fails = params.get("expected_fails")
                    utlv.check_result(result, expected_fails)
                    vmxml_live = vm_xml.VMXML.new_from_dumpxml(dom.name)
                    logging.debug("Current vmxml after hot-unplug dimm is %s\n" % vmxml_live)
                    expected_events_list.append("'device-removal-failed' for %s: dimm0")
                elif event == "watchdog":
                    vmxml.remove_all_device_by_type('watchdog')
                    watchdog_dev = Watchdog()
                    watchdog_dev.model_type = params.get("watchdog_model")
                    action = params.get("action")
                    watchdog_dev.action = action
                    vmxml.add_device(watchdog_dev)
                    vmxml.sync()
                    logging.debug("Current vmxml with watchdog dev is %s\n" % vmxml)
                    virsh.start(dom.name, **virsh_dargs)
                    session = dom.wait_for_login()
                    try:
                        session.cmd("echo 0 > /dev/watchdog")
                    except (ShellTimeoutError, ShellProcessTerminatedError) as details:
                        test.fail("Failed to trigger watchdog: %s" % details)
                    session.close()
                    # watchdog acts slowly, waiting for it.
                    time.sleep(30)
                    expected_events_list.append("'watchdog' for %s: " + "%s" % action)
                    if action == 'pause':
                        expected_events_list.append("'lifecycle' for %s: Suspended Watchdog")
                        virsh.resume(dom.name, **virsh_dargs)
                    else:
                        # action == 'reset'
                        expected_events_list.append("'reboot' for %s")
                elif event == "io-error":
                    part_size = params.get("part_size")
                    resume_event = params.get("resume_event")
                    suspend_event = params.get("suspend_event")
                    process.run("truncate -s %s %s" % (part_size, small_part), shell=True)
                    utlv.mkfs(small_part, part_format)
                    utils_misc.mount(small_part, mount_point, None)
                    add_disk(dom.name, new_disk, 'vdb', '--subdriver qcow2 --config', 'qcow2')
                    dom.start()
                    session = dom.wait_for_login()
                    session.cmd("mkfs.ext4 /dev/vdb && mount /dev/vdb /mnt && ls /mnt && "
                                "dd if=/dev/zero of=/mnt/test.img bs=1M count=50", ignore_all_errors=True)
                    time.sleep(5)
                    session.close()
                    expected_events_list.append("'io-error' for %s: " + "%s" % new_disk + r" \(virtio-disk1\) pause")
                    expected_events_list.append("'io-error-reason' for %s: " + "%s" % new_disk + r" \(virtio-disk1\) pause due to enospc")
                    expected_events_list.append(suspend_event)
                    process.run("df -hT")
                    virsh.resume(dom.name, **virsh_dargs)
                    time.sleep(5)
                    expected_events_list.append(resume_event)
                    expected_events_list.append("'io-error' for %s: " + "%s" % new_disk + r" \(virtio-disk1\) pause")
                    expected_events_list.append("'io-error-reason' for %s: " + "%s" % new_disk + r" \(virtio-disk1\) pause due to enospc")
                    expected_events_list.append(suspend_event)
                    ret = virsh.domstate(dom.name, "--reason", **virsh_dargs)
                    if ret.stdout.strip() != "paused (I/O error)":
                        test.fail("Domain state should still be paused due to I/O error!")
                else:
                    test.error("Unsupported event: %s" % event)
                # Event may not received immediately
                time.sleep(3)
        finally:
            if os.path.exists(save_path):
                os.unlink(save_path)
            if os.path.exists(new_disk):
                os.unlink(new_disk)
            if os.path.exists(dest_path):
                os.unlink(dest_path)
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
            if event in expected_events_list[0] and not strict_order:
                event_idx = 0
            if re.search("block-threshold", event):
                event_str = "block-threshold"
            else:
                event_str = "event " + event % ("domain '%s'" % dom_name)
            logging.info("Expected event: %s", event_str)
            match = re.search(event_str, output[event_idx:])
            if match:
                event_idx = event_idx + match.start(0) + len(match.group(0))
                continue
            else:
                test.fail("Not find expected event:%s. Is your "
                          "guest too slow to get started in %ss?" %
                          (event_str, event_timeout))
        # Extra event check for io-error resume
        if events_list == ['io-error']:
            event_str = "event 'lifecycle' for domain " + vm_name + ": Resumed Unpaused"
            if re.search(event_str, output[event_idx:]):
                test.fail("Extra 'resume' occurred after I/O error!")

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
        if utils_misc.is_mounted("/dev/loop0", mount_point, part_format):
            utils_misc.umount("/dev/loop0", mount_point, part_format)
        if os.path.exists(small_part):
            os.unlink(small_part)
