import logging
import random
import string
import os
import glob
import aexpect

from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.watchdog import Watchdog


def run(test, params, env):
    """
    Test watchdog device:

    1.Add watchdog device to the guest xml.
    2.Start the guest.
    3.Trigger the watchdog in the guest.
    4.Confirm the guest status.
    """

    def trigger_watchdog(model):
        """
        Trigger watchdog

        :param model: action when watchdog triggered
        """
        watchdog_device = "device %s" % model
        if action == "dump":
            watchdog_action = "watchdog-action pause"
        else:
            watchdog_action = "watchdog-action %s" % action
        vm_pid = vm.get_pid()
        with open("/proc/%s/cmdline" % vm_pid) as vm_cmdline_file:
            vm_cmdline = vm_cmdline_file.read()
            vm_cmdline = vm_cmdline.replace('\x00', ' ')
            if not all(option in vm_cmdline for option in (watchdog_device, watchdog_action)):
                test.fail("Can not find %s or %s in qemu cmd line"
                          % (watchdog_device, watchdog_action))
        cmd = "gsettings set org.gnome.settings-daemon.plugins.power button-power shutdown"
        session.cmd(cmd, ignore_all_errors=True)
        try:
            if model == "ib700":
                try:
                    session.cmd("modprobe ib700wdt")
                except aexpect.ShellCmdError:
                    session.close()
                    test.fail("Failed to load module ib700wdt")
            session.cmd("dmesg | grep -i %s && lsmod | grep %s" % (model, model))
            session.cmd("echo 1 > /dev/watchdog")
        except aexpect.ShellCmdError:
            session.close()
            test.fail("Failed to trigger watchdog")

    def confirm_guest_status():
        """
        Confirm the guest status after watchdog triggered
        """
        def _booting_completed():
            session = vm.wait_for_login()
            status, second_boot_time = session.cmd_status_output("uptime --since")
            logging.debug("The second boot time is %s", second_boot_time)
            session.close()
            return second_boot_time > first_boot_time

        def _inject_nmi():
            session = vm.wait_for_login()
            status, output = session.cmd_status_output("dmesg | grep -i nmi")
            session.close()
            if status == 0:
                logging.debug(output)
                return True
            return False

        def _inject_nmi_event():
            virsh_session.send_ctrl("^C")
            output = virsh_session.get_stripped_output()
            if "inject-nmi" not in output:
                return False
            return True

        def _check_dump_file(dump_path, domain_id):
            dump_file = glob.glob('%s%s-*' % (dump_path, domain_id))
            if len(dump_file):
                logging.debug("Find the auto core dump file:\n%s", dump_file[0])
                os.remove(dump_file[0])
                return True
            return False

        if action in ["poweroff", "shutdown"]:
            if not utils_misc.wait_for(lambda: vm.state() == "shut off", 180, 10):
                test.fail("Guest not shutdown after watchdog triggered")
        elif action == "reset":
            if not utils_misc.wait_for(_booting_completed, 600, 10):
                test.fail("Guest not reboot after watchdog triggered")
        elif action == "pause":
            if utils_misc.wait_for(lambda: vm.state() == "paused", 180, 10):
                cmd_output = virsh.domstate(vm_name, '--reason').stdout.strip()
                logging.debug("Check guest status: %s\n", cmd_output)
                if cmd_output != "paused (watchdog)":
                    test.fail("The domstate is not correct after dump by watchdog")
            else:
                test.fail("Guest not pause after watchdog triggered")
        elif action == "none" and utils_misc.wait_for(lambda: vm.state() == "shut off", 180, 10):
            test.fail("Guest shutdown unexpectedly")
        elif action == "inject-nmi":
            if not utils_misc.wait_for(_inject_nmi, 180, 10):
                test.fail("Guest not receive inject-nmi after watchdog triggered\n")
            elif not utils_misc.wait_for(_inject_nmi_event, 180, 10):
                test.fail("No inject-nmi watchdog event caught")
            virsh_session.close()
        elif action == "dump":
            domain_id = vm.get_id()
            dump_path = "/var/lib/libvirt/qemu/dump/"
            if not utils_misc.wait_for(lambda: _check_dump_file(dump_path, domain_id), 180, 10):
                test.fail("No auto core dump file found after watchdog triggered")

    name_length = params.get("name_length", "default")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    model = params.get("model")
    action = params.get("action")

    # Backup xml file
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # Rename the guest name to the length defined in the config file
    if name_length != "default":
        origin_name = vm_name
        name_length = int(params.get("name_length", "1"))
        vm_name = ''.join([random.choice(string.ascii_letters+string.digits)
                           for _ in range(name_length)])
        vm_xml.VMXML.vm_rename(vm, vm_name)
        # Generate the renamed xml file
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    # Add watchdog device to domain
    vmxml.remove_all_device_by_type('watchdog')
    watchdog_dev = Watchdog()
    watchdog_dev.model_type = model
    watchdog_dev.action = action
    chars = string.ascii_letters + string.digits + '-_'
    alias_name = 'ua-' + ''.join(random.choice(chars) for _ in list(range(64)))
    watchdog_dev.alias = {'name': alias_name}
    vmxml.add_device(watchdog_dev)
    vmxml.sync()
    try:
        vm.start()
        session = vm.wait_for_login()
        if action == "reset":
            status, first_boot_time = session.cmd_status_output("uptime --since")
            logging.info("The first boot time is %s\n", first_boot_time)
        if action == "inject-nmi":
            virsh_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC, auto_close=True)
            event_cmd = "event --event watchdog --all --loop"
            virsh_session.sendline(event_cmd)
        trigger_watchdog(model)
        confirm_guest_status()
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
