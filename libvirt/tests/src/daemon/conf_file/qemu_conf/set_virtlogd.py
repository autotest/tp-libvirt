import logging as log
import glob
import os
import stat
import time

from pwd import getpwuid
from xml.etree.ElementTree import parse

from avocado.utils import process

from virttest import utils_config
from virttest import utils_libvirtd
from virttest import utils_package
from virttest import utils_split_daemons
from virttest import virt_vm
from virttest import virsh

from virttest.staging import service
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.console import Console
from virttest.libvirt_xml.devices.graphics import Graphics
from virttest.libvirt_xml.devices.serial import Serial

from virttest import libvirt_version


# Define qemu log path.
QEMU_LOG_PATH = "/var/log/libvirt/qemu"


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


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
            serial.sources = console.sources = [
                {'attrs': {'path': guest_log_file, 'append': 'off'}}]
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

    def enable_virtlogd_specific_config_file():
        """
        Configure virtlogd using specific config file.

        """
        with open(virtlogd_config_file, "w") as fd:
            fd.write('VIRTLOGD_ARGS="--config /etc/libvirt/virtlogd-new.conf"')
            fd.write('\n')

        with open(virtlogd_config_file_new, "w") as fd:
            fd.write('log_level = 2')
            fd.write('\n')
            fd.write('log_outputs="1:file:/var/log/libvirt/virtlogd-new.log"')
            fd.write('\n')
        # Modify the selinux context
        cmd = "chcon system_u:object_r:virtlogd_etc_t:s0 %s" % virtlogd_config_file_new
        process.run(cmd, ignore_status=True, shell=True)
        # restart virtlogd
        service.Factory.create_service("virtlogd").restart()
        check_service_status("virtlogd", service_start=False)

    def check_virtlogd_started_with_config():
        """
        Check if virtlogd is started with --config argument

        """
        cmd = "ps -fC 'virtlogd'"
        cmd_result = process.run(cmd, shell=True).stdout_text.strip()
        matched_message = "--config %s" % virtlogd_config_file_new
        if matched_message not in cmd_result:
            test.fail("Check virtlogd is started with config :%s failed in output:%s"
                      % (matched_message, cmd_result))
        new_virtlogd_log_file = params.get("virtlogd_config_file_alternative_new")
        if not os.path.exists(new_virtlogd_log_file):
            test.fail("Failed to create new virtlogd log:%s" % new_virtlogd_log_file)
        str_to_grep = "virObject"
        if not libvirt.check_logfile(str_to_grep, new_virtlogd_log_file):
            test.fail("Check message log:%s failed in log file:%s"
                      % (str_to_grep, new_virtlogd_log_file))

    def enable_virtlogd_timeout():
        """
        Configure virtlogd using timeout parameter.

        """
        with open(virtlogd_config_file, "w") as fd:
            fd.write('VIRTLOGD_ARGS="--timeout=30"')
            fd.write('\n')
        # restart virtlogd
        service.Factory.create_service("virtlogd").restart()
        check_service_status("virtlogd", service_start=False)

    def check_virtlogd_started_with_timeout():
        """
        Check if virtlogd is started with timeout=30

        """
        cmd = "ps -fC 'virtlogd'"
        cmd_result = process.run(cmd, shell=True).stdout_text.strip()
        matched_message = "--timeout=30"
        if matched_message not in cmd_result:
            test.fail("Check virtlogd is started with config :%s failed in output:%s"
                      % (matched_message, cmd_result))
        # nothing to do in 40 seconds to make virtlogd timeout
        time.sleep(40)
        cmd = "ps aux|grep virtlogd"
        if process.run(cmd, shell=True).exit_status == 0:
            test.fail("Find unexpected virtlogd")

    def check_virtlogd_failed_invalid_config():
        """
        Check if virtlogd is failed status

        """
        virtlogd_config.max_clients = params.get("max_clients")
        cmd = ("systemctl status virtlogd | grep 'Active: active'")
        ret = process.run(cmd, ignore_status=True, shell=True, verbose=True)
        if ret.exit_status != 0:
            test.fail("virtlogd is not in active from log:%s" % ret.stdout_text.strip())

        action = params.get("action")
        process.run("systemctl %s virtlogd" % action, ignore_status=True, shell=True)
        time.sleep(4)
        cmd = ("systemctl status virtlogd | grep 'Active: failed'")
        result = process.run(cmd, ignore_status=True, shell=True, verbose=True)
        if result.exit_status != 0:
            test.fail("virtlogd is not in failed state from output: %s" % result.stdout_text.strip())

    def create_crash_vm():
        """
        create one VM which will crash when starting

        """
        # disable huge pages
        cmd_hugepage = " sysctl vm.nr_hugepages=0"
        process.run(cmd_hugepage, ignore_status=True, shell=True, verbose=True)
        VMXML.set_memoryBacking_tag(vm_name)

    def check_record_qenu_crash_log():
        """
        check recorded qemu crash log
        """
        # Start VM
        try:
            vm.start()
        except virt_vm.VMStartError as detail:
            logging.info("VM failed to start."
                         "Error: %s" % str(detail))
        crash_information = params.get("crash_information")
        if not libvirt.check_logfile(crash_information, guest_log_file):
            test.fail("Check expected message log:%s failed in log file:%s"
                      % (crash_information, guest_log_file))

    def lsof_qemu_log_file():
        """
        use lsof command to check qemu log file

        :return: lsof command return value
        """
        cmd_lsof = "lsof %s" % guest_log_file
        lsof_output = process.run(cmd_lsof, ignore_status=True, shell=True, verbose=True).stdout_text.strip()
        return lsof_output

    def stop_virtlogd():
        """
        Stop virtlogd service

        """
        # truncate qemu log file in order to avoid making later checking complex
        truncate_log_file(guest_log_file, '0')
        vm.start()
        vm.wait_for_login().close()
        lsof_output = lsof_qemu_log_file()
        if "virtlogd" not in lsof_output:
            test.fail("virtlogd does not open qemu log file:%s"
                      % guest_log_file)

    def check_stop_virtlogd():
        """
        Check something happen after stop virtlogd service

        """
        virtlogd_service = service.Factory.create_service("virtlogd")
        virtlogd_service.stop()
        vm.wait_for_login().close()
        lsof_output = lsof_qemu_log_file()
        if "virtlogd" in lsof_output:
            test.fail("guest log still write into qemu log file:%s after virtlogd stop"
                      % guest_log_file)
        vm.destroy()
        shut_down_msg = "shutting down, reason=shutdown"
        if not libvirt.check_logfile(shut_down_msg, guest_log_file):
            test.fail("Check VM destroy message log:%s failed in log file:%s"
                      % (shut_down_msg, guest_log_file))
        qemu_msg = "qemu-kvm: terminating on signal"
        if not libvirt.check_logfile(qemu_msg, guest_log_file, str_in_log=False):
            test.fail("Find unexpected:%s failed in log file:%s"
                      % (qemu_msg, guest_log_file))

        virtlogd_service.start()

    def check_file_exist(log_file):
        """
        check whether specified file exist or not

        :param log_file: specified log file name.
        """
        if not os.path.exists(log_file):
            test.fail("failed to find qemu log file: %s" % log_file)

    def truncate_log_file(file_path, size_in_unit="2M"):
        """
        truncate guest log file to default 2M

        :param file_path: specified file name.
        :param size_in_unit: expected size after being truncated.
        """
        truncate_2m_cmd = "truncate -s %s %s" % (size_in_unit, file_path)
        process.run(truncate_2m_cmd, ignore_status=True, shell=True, verbose=True)

    def enable_default_max_size_max_backups():
        """
        Enable default qemu log file max size and max backups

        """
        def _start_stop_vm():
            """
            start and stop to generate large logs
            """
            vm.start()
            vm.wait_for_login().close()
            vm.destroy()

        if vm.is_alive():
            vm.destroy(gracefully=False)
        # clean up log file exclude /var/log/libvirt/qemu/$guest_name.log
        file_list = glob.glob('%s/%s.log.*' % (QEMU_LOG_PATH, vm_name))
        for file_name in file_list:
            if os.path.exists(file_name):
                os.remove(file_name)
        # increase log file size to 2M to trigger generating new log file
        for i in range(0, 4):
            truncate_log_file(guest_log_file)
            _start_stop_vm()
            # check /var/log/libvirt/qemu/$guest_name.log.1 is generated
            if i < 3:
                guest_log_file_index = os.path.join(QEMU_LOG_PATH, "%s.log.%s" % (vm_name, i))
                check_file_exist(guest_log_file_index)

    def check_default_max_size_max_backups():
        """
        check default qemu log file max size and max backups

        """
        max_backups = int(params.get("max_backups"))
        # three are totally guest log file: max_backups + 1
        log_file_list = glob.glob('%s/%s.log*' % (QEMU_LOG_PATH, vm_name))
        if len(log_file_list) != (max_backups + 1):
            test.fail("the total related log file is not expected, and actual are: %s" % log_file_list)

    def enable_recreate_qemu_log():
        """
        Enable create new qemu log file again

        """
        vm.start()
        check_file_exist(guest_log_file)
        os.remove(guest_log_file)
        # trigger qemu log generation
        virsh.qemu_monitor_command(name=vm.name, cmd="info block", options='--hmp',
                                   **{'debug': True, 'ignore_status': True})
        if os.path.exists(guest_log_file):
            test.fail("guest log file: %s is wrongly recreated" % guest_log_file)

    def check_recreate_qemu_log():
        """
        check default qemu log will be recreated after VM destroy
        """
        # guest log file will be recreated once VM is destroyed, and log will be recorded accordingly
        virsh.destroy(vm_name)
        vm_destroy_msg = "shutting down, reason=destroyed"
        if not libvirt.check_logfile(vm_destroy_msg, guest_log_file):
            test.fail("Check VM destroy message log:%s failed in log file:%s"
                      % (vm_destroy_msg, guest_log_file))

    def check_opened_fd_of_qemu_log_file():
        """
        Check whether qemu log file is opened by virtlogd correctly

        """
        vm.start()
        vm.wait_for_login().close()
        lsof_start_vm_output = lsof_qemu_log_file()
        if "virtlogd" not in lsof_start_vm_output:
            test.fail("virtlogd does not open qemu log file after VM start:%s"
                      % guest_log_file)

        vm.destroy(gracefully=False)
        lsof_destroy_vm_output = lsof_qemu_log_file()
        if "virtlogd" in lsof_destroy_vm_output:
            test.fail("guest log still write into qemu log file:%s after VM stop"
                      % guest_log_file)

    def check_vm_destroy_log_into_qemu_log_file():
        """
        Check whether vm destroy log is written into qemu log file

        """
        # truncate qemu log file to size 0 in order to avoid making later checking complex
        truncate_log_file(guest_log_file, '0')
        vm.start()
        vm.wait_for_login().close()
        virsh.destroy(vm_name)
        vm_destroy_msg = ".*qemu-kvm: terminating on signal.*\n.*shutting down, reason=destroyed.*"
        if not libvirt.check_logfile(vm_destroy_msg, guest_log_file):
            test.fail("Check VM destroy message log:%s failed in log file:%s"
                      % (vm_destroy_msg, guest_log_file))

    def check_start_vm_twice_log_into_qemu_log_file():
        """
        Check whether vm start twice is written into qemu log file

        """
        vm.start()
        vm.wait_for_login().close()
        # truncate qemu log file to size 0 in order to avoid making later checking complex
        truncate_log_file(guest_log_file, '0')
        virsh.destroy(vm_name)
        vm.start()
        vm.wait_for_login().close()
        vm_destroy_start_msg = ".*qemu-kvm: terminating on signal.*\n.*" + \
            "shutting down, reason=destroyed.*\n.*name guest=%s.*" % vm_name
        if not libvirt.check_logfile(vm_destroy_start_msg, guest_log_file):
            test.fail("Check VM destroy and start message log:%s failed in log file:%s"
                      % (vm_destroy_start_msg, guest_log_file))

    def check_record_save_restore_guest_log():
        """
        Check whether vm save and restore is written into qemu log file

        """
        vm.start()
        vm.wait_for_login().close()
        # truncate qemu log file to size 0 in order to avoid making later checking complex
        truncate_log_file(guest_log_file, '0')
        save_path = params.get("save_vm_path")
        virsh.save(vm_name, save_path)
        virsh.restore(save_path)
        vm.wait_for_login().close()
        vm_save_restore_msg = ".*qemu-kvm: terminating on signal.*\n.*" + \
            "shutting down, reason=saved.*\n.*name guest=%s.*" % vm_name
        if not libvirt.check_logfile(vm_save_restore_msg, guest_log_file):
            test.fail("Check VM save and restore message log:%s failed in log file:%s"
                      % (vm_save_restore_msg, guest_log_file))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    expected_result = params.get("expected_result", "virtlogd_disabled")
    stdio_handler = params.get("stdio_handler", "not_set")
    matched_msg = params.get("matched_msg", "Powering off")
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
    virtlogd_config = utils_config.VirtLogdConfig()
    try:
        if stdio_handler != 'not_set':
            config['stdio_handler'] = "'%s'" % stdio_handler
        if restart_libvirtd or stop_libvirtd:
            virtlogd_pid = check_service_status("virtlogd", service_start=True)
            logging.info("virtlogd pid: %s", virtlogd_pid)
            service_name = "virtqemud" if utils_split_daemons.is_modular_daemon else "libvirtd"
            check_service_status(service_name, service_start=True)

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
            elif expected_result == 'virtlogd_restart':
                # check virtlogd status
                virtlogd_pid = check_service_status("virtlogd", service_start=True)
                logging.info("virtlogd PID: %s", virtlogd_pid)
                # restart virtlogd
                ret = service.Factory.create_service("virtlogd").restart()
                if ret is False:
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
            elif expected_result == 'virtlogd_disabled':
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
            elif expected_result in ['virtlogd_specific_config_file_enable', 'specific_timeout']:
                virtlogd_config_file = params.get("virtlogd_config_file")
                virtlogd_config_bak_file = params.get("virtlogd_config_bak_file")
                if os.path.exists(virtlogd_config_file):
                    # backup config file
                    os.rename(virtlogd_config_file, virtlogd_config_bak_file)
                virtlogd_config_file_new = params.get("virtlogd_config_file_new")
            elif expected_result == 'virtlogd_specific_config_file_enable':
                enable_virtlogd_specific_config_file()
                check_virtlogd_started_with_config()
            elif expected_result == 'specific_timeout':
                enable_virtlogd_timeout()
                check_virtlogd_started_with_timeout()
            elif expected_result == "invalid_virtlogd_conf":
                check_virtlogd_failed_invalid_config()
            elif expected_result == "record_qenu_crash_log":
                create_crash_vm()
                check_record_qenu_crash_log()
            elif expected_result == "stop_virtlogd":
                stop_virtlogd()
                check_stop_virtlogd()
            elif expected_result == "default_max_size_max_backups":
                enable_default_max_size_max_backups()
                check_default_max_size_max_backups()
            elif expected_result == "recreate_qemu_log":
                enable_recreate_qemu_log()
                check_recreate_qemu_log()
            elif expected_result == "opened_fd_of_qemu_log_file":
                check_opened_fd_of_qemu_log_file()
            elif expected_result == "vm_destroy_log_into_qemu_log_file":
                check_vm_destroy_log_into_qemu_log_file()
            elif expected_result == "start_vm_twice_log_into_qemu_log_file":
                check_start_vm_twice_log_into_qemu_log_file()
            elif expected_result == "record_save_restore_guest_log":
                check_record_save_restore_guest_log()
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
            if stdio_handler != "file":
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
            open_cmd = 'qemu-kvm' if stdio_handler == "file" else 'virtlogd'
            cmd = ("lsof -w %s|grep %s" % (guest_log_file, open_cmd))
            errorMessage = "VM log file: %s is not opened by:%s." % (guest_log_file, open_cmd)
            configure(cmd, guest_log_file, errorMessage)

            # Check VM started log is written into log file correctly.
            if not with_console_log:
                check_info_in_vm_log_file(vm_name, guest_log_file, matchedMsg="char device redirected to /dev/pts")

            if stdio_handler != "file":
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

            if stdio_handler != "file":
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
                                          matchedMsg=matched_msg)
            else:
                check_info_in_vm_log_file(vm_name, guest_log_file, matchedMsg="shutting down")

            if stdio_handler != "file":
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
        if expected_result in ['virtlogd_specific_config_file_enable', 'specific_timeout']:
            if os.path.exists(virtlogd_config_file):
                os.remove(virtlogd_config_file)
            if os.path.exists(virtlogd_config_file_new):
                os.remove(virtlogd_config_file_new)
            if virtlogd_config_bak_file:
                if os.path.exists(virtlogd_config_file):
                    os.rename(virtlogd_config_bak_file, virtlogd_config_file)
                service.Factory.create_service("virtlogd").restart()
        if expected_result == "invalid_virtlogd_conf":
            virtlogd_config.restore()
            service.Factory.create_service("virtlogd").restart()
