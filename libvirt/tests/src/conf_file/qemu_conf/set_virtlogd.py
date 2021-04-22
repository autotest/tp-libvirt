import logging
import os
import stat
import time

from pwd import getpwuid
from xml.etree.ElementTree import parse

from avocado.utils import process

from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_package
from virttest import virt_vm
from virttest.staging import service
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.console import Console
from virttest.libvirt_xml.devices.graphics import Graphics
from virttest.libvirt_xml.devices.serial import Serial

from virttest import libvirt_version


# Define qemu log path.
QEMU_LOG_PATH = "/var/log/libvirt/qemu"


def run(test, params, env):
    """
    Test stdio_handler parameter in qemu.conf to use for handling stdout/stderr
    output from QEMU processes.

    1) Change stdio_handler in qemu.conf;
    2) Restart libvirtd daemon;
    3) Check if libvirtd successfully started;
    4) Check if virtlogd.socket is running;
    5) Configure pty serial and console;
    6) Check if VM log file exists and has correct permission and owner;
    7) Check if VM log file is opened by virtlogd;
    8) Check if VM start log is written into VM log file correctly;
    9) Check if QEMU use pipe provided by virtlogd daemon for logging;
    10) Check if VM shutdown log is written into VM log file correctly;
    11) Check if pipe file can be closed gracefully after VM shutdown;
    12) Check if VM restart log can be appended to the end of previous log file;
    """

    def clean_up_vm_log_file(vm_name, guest_log_file):
        """
        Clean up VM log file.

        :params vm_name: guest name
        :params guest_log_file: the path of VM log file
        """
        # Delete VM log file if exists.
        if os.path.exists(guest_log_file):
            os.remove(guest_log_file)

    def configure(cmd, guest_log_file=None, errorMsg=None):
        """
        Configure qemu log.

        :param cmd: execute command string.
        :param guest_log_file: the path of VM log file.
        :param errorMsg: error message if failed
        :return: pipe node.
        """
        # If guest_log_file is not None,check if VM log file exists or not.
        if guest_log_file and not os.path.exists(guest_log_file):
            test.error("Expected VM log file: %s not exists" % guest_log_file)
        # If errorMsg is not None, check command running result.
        elif errorMsg:
            if process.run(cmd, ignore_status=True, shell=True).exit_status:
                test.error(errorMsg)
        # Get pipe node.
        else:
            result = process.run(cmd, timeout=90, ignore_status=True, shell=True)
            ret, output = result.exit_status, result.stdout_text.strip()
            if ret:
                test.fail("Failed to get pipe node")
            else:
                return output

    def configure_serial_console(vm_name, dev_type, guest_log_file=None):
        """
        Configure serial console.

        :params vm_name: guest name
        :params dev_type: device type
        :params guest_log_file: the path of VM log file
        """
        guest_xml = VMXML.new_from_inactive_dumpxml(vm_name)
        guest_xml.remove_all_device_by_type('serial')
        guest_xml.remove_all_device_by_type('console')

        serial = Serial(dev_type)
        serial.target_port = '0'

        console = Console(dev_type)
        console.target_port = '0'
        console.target_type = 'serial'

        if dev_type == "file" and guest_log_file is not None:
            serial.sources = console.sources = [{'path': guest_log_file, 'append': 'off'}]
        guest_xml.add_device(serial)
        guest_xml.add_device(console)
        guest_xml.sync()

    def check_vm_log_file_permission_and_owner(vm_name, guest_log_file):
        """
        Check VM log file permission and owner.

        :params vm_name: guest name
        :params guest_log_file: the path of VM log file
        """
        # Check VM log file permission.
        logging.info("guest log file: %s", guest_log_file)
        if not os.path.exists(guest_log_file):
            test.error("Expected VM log file: %s not exists" % guest_log_file)
        permission = oct(stat.S_IMODE(os.lstat(guest_log_file).st_mode))
        if permission != '0600' and permission != '0o600':
            test.fail("VM log file: %s expect to get permission:0600, got %s ."
                      % (guest_log_file, permission))
        # Check VM log file owner.
        owner = getpwuid(stat.S_IMODE(os.lstat(guest_log_file).st_uid)).pw_name
        if owner != 'root':
            test.fail("VM log file: %s expect to get owner:root, got %s ."
                      % (guest_log_file, owner))

    def check_info_in_vm_log_file(vm_name, guest_log_file, cmd=None, matchedMsg=None):
        """
        Check if log information is written into log file correctly.

        :params vm_name: guest name
        :params guest_log_file: the path of VM log file
        :params cmd: execute command string
        :params matchedMsg: match message
        """
        # Check VM log file is opened by virtlogd.
        if not os.path.exists(guest_log_file):
            test.fail("Expected VM log file: %s not exists" % guest_log_file)

        if cmd is None:
            cmd = ("grep -nr '%s' %s" % (matchedMsg, guest_log_file))
        else:
            cmd = (cmd + " %s |grep '%s'" % (guest_log_file, matchedMsg))
        if process.run(cmd, ignore_status=True, shell=True).exit_status:
            test.fail("Failed to get VM started log from VM log file: %s."
                      % guest_log_file)

    def check_pipe_closed(pipe_node):
        """
        Check pipe used by QEMU is closed gracefully after VM shutdown.
        """
        # Check pipe node can not be listed after VM shutdown.
        cmd = ("lsof +c0 -w |grep pipe|grep virtlogd|grep %s" % pipe_node)
        if not process.run(cmd, timeout=90, ignore_status=True, shell=True).exit_status:
            test.fail("pipe node: %s is not closed in virtlogd gracefully." % pipe_node)

        cmd = ("lsof +c0 -w |grep pipe|grep %s|grep %s" % (emulator[0:15], pipe_node))
        if not process.run(cmd, timeout=90, ignore_status=True, shell=True).exit_status:
            test.fail("pipe node: %s is not closed in qemu gracefully." % pipe_node)

    def check_service_status(service_name, service_start=False):
        """
        Check service status and return service PID

        :param service_name: service name
        :param service_start: whether to start service or not
        :return: service PID
        """
        # Check service status
        cmd = ("systemctl status %s | grep 'Active: active'" % service_name)
        ret = process.run(cmd, ignore_status=True, shell=True, verbose=True)
        if ret.exit_status:
            # If service isn't active and setting 'service_start', start service.
            if service_start:
                ret = process.run("systemctl start %s" % service_name, shell=True)
                if ret.exit_status:
                    test.fail("%s start failed." % service_name)
            # If service isn't active and don't set 'service_start', return error.
            else:
                test.fail("%s is not active." % service_name)
        cmd = ("systemctl status %s | grep 'Main PID:'" % service_name)
        ret = process.run(cmd, ignore_status=True, shell=True, verbose=True)
        if ret.exit_status:
            test.fail("Get %s status failed." % service_name)
        return ret.stdout_text.split()[2]

    def reload_and_check_virtlogd():
        """
        Reload and check virtlogd status
        """
        virtlogd_pid = check_service_status("virtlogd", service_start=True)
        logging.info("virtlogd PID: %s", virtlogd_pid)
        ret = process.run("systemctl reload virtlogd", shell=True)
        if ret.exit_status:
            test.fail("virtlogd reload failed.")
        reload_virtlogd_pid = check_service_status("virtlogd", service_start=True)
        logging.info("After reload, virtlogd PID: %s", reload_virtlogd_pid)
        if virtlogd_pid != reload_virtlogd_pid:
            test.fail("After reload, virtlogd PID changed.")

    def configure_spice(vm_name):
        """
        Configure spice

        :params vm_name: guest name
        """
        vm_spice_xml = VMXML.new_from_inactive_dumpxml(vm_name)
        vm_spice_xml.remove_all_device_by_type('graphics')

        graphic = Graphics(type_name='spice')
        graphic.autoport = "yes"
        graphic.port = "-1"
        graphic.tlsPort = "-1"
        vm_spice_xml.add_device(graphic)
        vm_spice_xml.sync()

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    expected_result = params.get("expected_result", "virtlogd_disabled")
    stdio_handler = params.get("stdio_handler", "not_set")
    start_vm = "yes" == params.get("start_vm", "yes")
    reload_virtlogd = "yes" == params.get("reload_virtlogd", "no")
    restart_libvirtd = "yes" == params.get("restart_libvirtd", "no")
    stop_libvirtd = "yes" == params.get("stop_libvirtd", "no")
    with_spice = "yes" == params.get("with_spice", "no")
    with_console_log = "yes" == params.get("with_console_log", "no")

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    emulator_path = parse(vm_xml.get_devices(device_type='emulator')[0]['xml']).getroot().text
    emulator = os.path.basename(emulator_path)
    vm_xml_backup = vm_xml.copy()
    if with_console_log:
        guest_log_file = os.path.join(QEMU_LOG_PATH, "%s-console.log" % vm_name)
    else:
        guest_log_file = os.path.join(QEMU_LOG_PATH, "%s.log" % vm_name)

    config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        if stdio_handler != 'not_set':
            config['stdio_handler'] = "'%s'" % stdio_handler
        if restart_libvirtd or stop_libvirtd:
            virtlogd_pid = check_service_status("virtlogd", service_start=True)
            logging.info("virtlogd pid: %s", virtlogd_pid)
            check_service_status("libvirtd", service_start=True)

        # Restart libvirtd to make change valid.
        if not libvirtd.restart():
            if expected_result != 'unbootable':
                test.fail('Libvirtd is expected to be started '
                          'with stdio_handler=%s' % stdio_handler)
            return
        if expected_result == 'unbootable':
            test.fail('Libvirtd is not expected to be started '
                      'with stdio_handler=%s' % stdio_handler)

        if not start_vm:
            if reload_virtlogd:
                reload_and_check_virtlogd()
            if expected_result == 'virtlogd_restart':
                # check virtlogd status
                virtlogd_pid = check_service_status("virtlogd", service_start=True)
                logging.info("virtlogd PID: %s", virtlogd_pid)
                # restart virtlogd
                ret = process.run("systemctl restart virtlogd", ignore_status=True, shell=True)
                if ret.exit_status:
                    test.fail("failed to restart virtlogd.")
                # check virtlogd status
                new_virtlogd_pid = check_service_status("virtlogd", service_start=False)
                logging.info("new virtlogd PID: %s", new_virtlogd_pid)
                if virtlogd_pid == new_virtlogd_pid:
                    test.fail("virtlogd pid don't change.")
                cmd = "ps -o ppid,pid,pgid,sid,tpgid,tty,stat,command -C virtlogd"
                ret = process.run(cmd, ignore_status=True, shell=True)
                if ret.exit_status:
                    test.fail("virtlogd don't exist.")
            if expected_result == 'virtlogd_disabled':
                # check virtlogd status
                virtlogd_pid = check_service_status("virtlogd", service_start=True)
                logging.info("virtlogd PID: %s", virtlogd_pid)
                # disabled virtlogd
                service_manager = service.Factory.create_generic_service()
                service_manager.stop('virtlogd')
                # check virtlogd status
                if service_manager.status('virtlogd'):
                    test.fail("virtlogd status is not inactive.")
                cmd = "ps -C virtlogd"
                ret = process.run(cmd, ignore_status=True, shell=True)
                if not ret.exit_status:
                    test.fail("virtlogd still exist.")
        else:
            # Stop all VMs if VMs are already started.
            for tmp_vm in env.get_all_vms():
                if tmp_vm.is_alive():
                    tmp_vm.destroy(gracefully=False)

            # Sleep a few seconds to let VM syn underlying data
            time.sleep(3)

            # Remove VM previous log file.
            clean_up_vm_log_file(vm_name, guest_log_file)

            # Check if virtlogd socket is running.
            cmd = ("systemctl status virtlogd.socket|grep 'Active: active'")
            configure(cmd, errorMsg="virtlogd.socket is not running")

            # Configure spice
            if with_spice:
                configure_spice(vm_name)

            # Configure serial console.
            if with_console_log:
                configure_serial_console(vm_name, 'file', guest_log_file)
            else:
                configure_serial_console(vm_name, 'pty')

            logging.info("final vm:")
            logging.info(VMXML.new_from_inactive_dumpxml(vm_name))

            # Start VM.
            try:
                vm.start()
            except virt_vm.VMStartError as detail:
                test.fail("VM failed to start."
                          "Error: %s" % str(detail))
            # Wait for write log to console log file
            if with_console_log:
                vm.wait_for_login()

            # Check VM log file has right permission and owner.
            check_vm_log_file_permission_and_owner(vm_name, guest_log_file)
            utils_package.package_install(['lsof'])
            # Check VM log file is opened by virtlogd.
            cmd = ("lsof -w %s|grep 'virtlogd'" % guest_log_file)
            errorMessage = "VM log file: %s is not opened by:virtlogd." % guest_log_file
            configure(cmd, guest_log_file, errorMessage)

            # Check VM started log is written into log file correctly.
            if not with_console_log:
                check_info_in_vm_log_file(vm_name, guest_log_file, matchedMsg="char device redirected to /dev/pts")

            # Get pipe node opened by virtlogd for VM log file.
            pipe_node_field = "$9"
            # On latest release,No.8 field in lsof returning is pipe node number.
            if libvirt_version.version_compare(4, 3, 0):
                pipe_node_field = "$8"
            cmd = ("lsof +c0 -w |grep pipe|grep virtlogd|tail -n 1|awk '{print %s}'" % pipe_node_field)
            pipe_node = configure(cmd)

            if restart_libvirtd or stop_libvirtd:
                cmd2 = "lsof %s | grep virtlogd | awk '{print $2}'" % guest_log_file
                if restart_libvirtd:
                    libvirtd.restart()
                if stop_libvirtd:
                    pid_in_log = configure(cmd2)
                    logging.info("virtlogd pid in guest log: %s" % pid_in_log)
                    libvirtd.stop()
                new_virtlogd_pid = check_service_status("virtlogd", service_start=True)
                logging.info("New virtlogd PID: %s", new_virtlogd_pid)
                if restart_libvirtd:
                    new_pipe_node = configure(cmd)
                    logging.info("After libvirtd restart, pipe node: %s", new_pipe_node)
                    if pipe_node != new_pipe_node and new_pipe_node != new_virtlogd_pid:
                        test.fail("After libvirtd restart, pipe node changed.")
                if stop_libvirtd:
                    new_pid_in_log = configure(cmd2)
                    logging.info("After libvirtd stop, new virtlogd pid in guest log: %s" % new_pid_in_log)
                    if pid_in_log != new_virtlogd_pid or pid_in_log != new_pid_in_log:
                        test.fail("After libvirtd stop, virtlogd PID changed.")

            if with_spice or with_console_log:
                reload_and_check_virtlogd()

            # Check if qemu use pipe node provided by virtlogd.
            cmd = ("lsof +c0 -w |grep pipe|grep %s|grep %s" % (emulator[0:15], pipe_node))
            errorMessage = ("Can not find matched pipe node: %s "
                            "from pipe list used by %s." % (emulator, pipe_node))
            configure(cmd, errorMsg=errorMessage)

            # Shutdown VM.
            if not vm.shutdown():
                vm.destroy(gracefully=True)

            # Check qemu log works well
            if with_spice:
                check_info_in_vm_log_file(vm_name, guest_log_file,
                                          matchedMsg="%s: terminating on signal 15 from pid" % emulator)

            # Check VM shutdown log is written into log file correctly.
            if with_console_log:
                check_info_in_vm_log_file(vm_name, guest_log_file,
                                          matchedMsg="Powering off")
            else:
                check_info_in_vm_log_file(vm_name, guest_log_file, matchedMsg="shutting down")

            # Check pipe is closed gracefully after VM shutdown.
            check_pipe_closed(pipe_node)

            # Start VM again.
            try:
                vm.start()
            except virt_vm.VMStartError as detail:
                test.fail("VM failed to start."
                          "Error: %s" % str(detail))
            # Check the new VM start log is appended to the end of the VM log file.
            if not with_console_log:
                check_info_in_vm_log_file(vm_name, guest_log_file, cmd="tail -n 5",
                                          matchedMsg="char device redirected to /dev/pts")

    finally:
        config.restore()
        libvirtd.restart()
        vm_xml_backup.sync()
