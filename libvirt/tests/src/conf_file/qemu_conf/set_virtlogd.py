import logging
import os
import stat
import time

from pwd import getpwuid

from avocado.utils import process

from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_package
from virttest import virt_vm
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.console import Console

from provider import libvirt_version


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

    def clean_up_vm_log_file(vm_name):
        """Clean up VM log file."""
        # Delete VM log file if exists.
        global QEMU_LOG_PATH
        guest_log_file = os.path.join(QEMU_LOG_PATH, "%s.log" % vm_name)
        if os.path.exists(guest_log_file):
            os.remove(guest_log_file)

    def configure(cmd, guest_log_file=None, errorMsg=None):
        """
        Configure qemu log.
        :param cmd. execute command string.
        :param guest_log_file. the path of VM log file.
        :param errorMsg. error message if failed
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
            ret, output = result.exit_status, result.stdout_text
            if ret:
                test.fail("Failed to get pipe node")
            else:
                return output

    def configure_serial_console(vm_name):
        """Configure serial console"""
        # Check the primary serial and set it to pty.
        VMXML.set_primary_serial(vm_name, 'pty', '0', None)
        # Configure VM pty console.
        vm_pty_xml = VMXML.new_from_inactive_dumpxml(vm_name)
        vm_pty_xml.remove_all_device_by_type('console')

        console = Console()
        console.target_port = '0'
        console.target_type = 'serial'
        vm_pty_xml.add_device(console)
        vm_pty_xml.sync()

    def check_vm_log_file_permission_and_owner(vm_name):
        """Check VM log file permission and owner."""
        # Check VM log file permission.
        global QEMU_LOG_PATH
        guest_log_file = os.path.join(QEMU_LOG_PATH, "%s.log" % vm_name)
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

    def check_info_in_vm_log_file(vm_name, cmd=None, matchedMsg=None):
        """
        Check if log information is written into log file correctly.
        """
        # Check VM log file is opened by virtlogd.
        global QEMU_LOG_PATH
        guest_log_file = os.path.join(QEMU_LOG_PATH, "%s.log" % vm_name)
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
        cmd = ("lsof  -w |grep pipe|grep virtlogd|grep %s" % pipe_node)
        if not process.run(cmd, timeout=90, ignore_status=True, shell=True).exit_status:
            test.fail("pipe node: %s is not closed in virtlogd gracefully." % pipe_node)

        cmd = ("lsof  -w |grep pipe|grep qemu-kvm|grep %s" % pipe_node)
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

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    expected_result = params.get("expected_result", "virtlogd_disabled")
    stdio_handler = params.get("stdio_handler", "not_set")
    start_vm = "yes" == params.get("start_vm", "yes")
    reload_virtlogd = "yes" == params.get("reload_virtlogd", "no")
    restart_libvirtd = "yes" == params.get("restart_libvirtd", "no")
    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    guest_log_file = os.path.join(QEMU_LOG_PATH, "%s.log" % vm_name)

    config = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        if stdio_handler != 'not_set':
            config['stdio_handler'] = "'%s'" % stdio_handler
        if restart_libvirtd:
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
                virtlogd_pid = check_service_status("virtlogd", service_start=True)
                logging.info("virtlogd PID: %s", virtlogd_pid)
                ret = process.run("systemctl reload virtlogd", shell=True)
                if ret.exit_status:
                    test.fail("virtlogd reload failed.")
                reload_virtlogd_pid = check_service_status("virtlogd", service_start=True)
                logging.info("After reload, virtlogd PID: %s", reload_virtlogd_pid)
                if virtlogd_pid != reload_virtlogd_pid:
                    test.fail("After reload, virtlogd PID changed.")
        else:
            # Stop all VMs if VMs are already started.
            for tmp_vm in env.get_all_vms():
                if tmp_vm.is_alive():
                    tmp_vm.destroy(gracefully=False)

            # Sleep a few seconds to let VM syn underlying data
            time.sleep(3)

            # Remove VM previous log file.
            clean_up_vm_log_file(vm_name)

            # Check if virtlogd socket is running.
            cmd = ("systemctl status virtlogd.socket|grep 'Active: active'")
            configure(cmd, errorMsg="virtlogd.socket is not running")

            # Configure serial console.
            configure_serial_console(vm_name)

            logging.info("final vm:")
            logging.info(VMXML.new_from_inactive_dumpxml(vm_name))

            # Start VM.
            try:
                vm.start()
            except virt_vm.VMStartError as detail:
                test.fail("VM failed to start."
                          "Error: %s" % str(detail))
            # Check VM log file has right permission and owner.
            check_vm_log_file_permission_and_owner(vm_name)
            utils_package.package_install(['lsof'])
            # Check VM log file is opened by virtlogd.
            cmd = ("lsof -w %s|grep 'virtlogd'" % guest_log_file)
            errorMessage = "VM log file: %s is not opened by:virtlogd." % guest_log_file
            configure(cmd, guest_log_file, errorMessage)

            # Check VM started log is written into log file correctly.
            check_info_in_vm_log_file(vm_name, matchedMsg="char device redirected to /dev/pts")

            # Get pipe node opened by virtlogd for VM log file.
            pipe_node_field = "$9"
            # On latest release,No.8 field in lsof returning is pipe node number.
            if libvirt_version.version_compare(4, 3, 0):
                pipe_node_field = "$8"
            cmd = ("lsof  -w |grep pipe|grep virtlogd|tail -n 1|awk '{print %s}'" % pipe_node_field)
            pipe_node = configure(cmd)

            if restart_libvirtd:
                libvirtd.restart()
                new_virtlogd_pid = check_service_status("virtlogd", service_start=True)
                logging.info("After libvirtd restart, virtlogd PID: %s", new_virtlogd_pid)
                new_pipe_node = configure(cmd)
                logging.info("After libvirtd restart, pipe node: %s", new_pipe_node)
                if pipe_node != new_pipe_node and new_pipe_node != new_virtlogd_pid:
                    test.fail("After libvirtd restart, pipe node changed.")

            # Check if qemu-kvm use pipe node provided by virtlogd.
            cmd = ("lsof  -w |grep pipe|grep qemu-kvm|grep %s" % pipe_node)
            errorMessage = ("Can not find matched pipe node: %s "
                            "from pipe list used by qemu-kvm." % pipe_node)
            configure(cmd, errorMsg=errorMessage)

            # Shutdown VM.
            if not vm.shutdown():
                vm.destroy(gracefully=True)

            # Check VM shutdown log is written into log file correctly.
            check_info_in_vm_log_file(vm_name, matchedMsg="shutting down")

            # Check pipe is closed gracefully after VM shutdown.
            check_pipe_closed(pipe_node)

            # Start VM again.
            try:
                vm.start()
            except virt_vm.VMStartError as detail:
                test.fail("VM failed to start."
                          "Error: %s" % str(detail))
            # Check the new VM start log is appended to the end of the VM log file.
            check_info_in_vm_log_file(vm_name, cmd="tail -n 5",
                                      matchedMsg="char device redirected to /dev/pts")

    finally:
        config.restore()
        libvirtd.restart()
        vm_xml_backup.sync()
