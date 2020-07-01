import re
import os
import shutil
import logging
import platform
import signal
import time

from aexpect import ShellTimeoutError
from aexpect import ShellProcessTerminatedError

from avocado.utils import process

from virttest import data_dir
from virttest import ssh_key
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_config
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.panic import Panic

from virttest import libvirt_version

find_dump_file = False


def check_crash_state(cmd_output, crash_action, vm_name, dump_file=""):
    """
    Check the domain state about crash actions.
    """
    expect_output = ""
    crash_state = False
    global find_dump_file

    if crash_action in ["destroy", "coredump-destroy"]:
        expect_output = "shut off (crashed)"
    elif crash_action in ["restart", "coredump-restart"]:
        expect_output = "running (crashed)"
    elif crash_action in ["preserve", "rename-restart"]:
        expect_output = "crashed (panicked)"
    logging.info("Expected state: %s", expect_output)

    def _wait_for_state():
        cmd_output = virsh.domstate(vm_name, '--reason').stdout.strip()
        return cmd_output.strip() == expect_output

    # There is a middle state 'crashed (panicked)' for these two actions
    if crash_action in ['coredump-destroy', 'coredump-restart']:
        middle_state = 'crashed (panicked)'
        text = 'VM in middle state: %s' % middle_state
        if cmd_output.strip() == middle_state:
            utils_misc.wait_for(_wait_for_state, 60, text=text)
            cmd_output = virsh.domstate(vm_name, '--reason').stdout.strip()

    logging.debug("Actual state: %s", cmd_output.strip())
    if cmd_output.strip() == expect_output:
        crash_state = True
    if dump_file:
        result = process.run("ls %s" % dump_file,
                             ignore_status=True, shell=True)
        if result.exit_status == 0:
            logging.debug("Find the auto dump core file:\n%s", result.stdout_text)
            find_dump_file = True
        else:
            logging.error("Not find coredump file: %s", dump_file)
    return crash_state


def run(test, params, env):
    """
    Test command: virsh domstate.

    1.Prepare test environment.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh domstate operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    libvirtd_state = params.get("libvirtd", "on")
    vm_ref = params.get("domstate_vm_ref")
    status_error = (params.get("status_error", "no") == "yes")
    extra = params.get("domstate_extra", "")
    vm_action = params.get("domstate_vm_action", "")
    vm_oncrash_action = params.get("domstate_vm_oncrash")
    reset_action = "yes" == params.get("reset_action", "no")
    dump_option = params.get("dump_option", "")
    start_action = params.get("start_action", "normal")
    kill_action = params.get("kill_action", "normal")
    check_libvirtd_log = params.get("check_libvirtd_log", "no")
    err_msg = params.get("err_msg", "")
    remote_uri = params.get("remote_uri")

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "hex_id":
        vm_ref = hex(int(domid))
    elif vm_ref.find("invalid") != -1:
        vm_ref = params.get(vm_ref)
    elif vm_ref == "name":
        vm_ref = vm_name
    elif vm_ref == "uuid":
        vm_ref = domuuid

    # Back up xml file.
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # Back up qemu.conf
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()

    # Config libvirtd log
    if check_libvirtd_log == "yes":
        libvirtd_conf = utils_config.LibvirtdConfig()
        libvirtd_log_file = os.path.join(data_dir.get_tmp_dir(), "libvirtd.log")
        libvirtd_conf["log_level"] = '1'
        libvirtd_conf["log_filters"] = ('"1:json 1:libvirt 1:qemu 1:monitor '
                                        '3:remote 4:event"')
        libvirtd_conf["log_outputs"] = '"1:file:%s"' % libvirtd_log_file
        logging.debug("the libvirtd config file content is:\n %s" %
                      libvirtd_conf)
        libvirtd.restart()

    # Get image file
    image_source = vm.get_first_disk_devices()['source']
    logging.debug("image source: %s" % image_source)
    new_image_source = image_source + '.rename'

    dump_path = os.path.join(data_dir.get_tmp_dir(), "dump/")
    logging.debug("dump_path: %s", dump_path)
    try:
        os.mkdir(dump_path)
    except OSError:
        # If the path already exists then pass
        pass
    dump_file = ""
    try:
        # Let's have guest memory less so that dumping core takes
        # time which doesn't timeout the testcase
        if vm_oncrash_action in ['coredump-destroy', 'coredump-restart']:
            memory_value = int(params.get("memory_value", "2097152"))
            memory_unit = params.get("memory_unit", "KiB")
            vmxml.set_memory(memory_value)
            vmxml.set_memory_unit(memory_unit)
            logging.debug(vmxml)
            vmxml.sync()

        if vm_action == "crash":
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vmxml.on_crash = vm_oncrash_action
            if not vmxml.xmltreefile.find('devices').findall('panic'):
                # Add <panic> device to domain
                panic_dev = Panic()
                if "ppc" not in platform.machine():
                    panic_dev.addr_type = "isa"
                    panic_dev.addr_iobase = "0x505"
                vmxml.add_device(panic_dev)
            vmxml.sync()
            # Config auto_dump_path in qemu.conf
            qemu_conf.auto_dump_path = dump_path
            libvirtd.restart()
            if vm_oncrash_action in ['coredump-destroy', 'coredump-restart']:
                dump_file = dump_path + "*" + vm_name[:20] + "-*"
            # Start VM and check the panic device
            virsh.start(vm_name, ignore_status=False)
            vmxml_new = vm_xml.VMXML.new_from_dumpxml(vm_name)
            # Skip this test if no panic device find
            if not vmxml_new.xmltreefile.find('devices').findall('panic'):
                test.cancel("No 'panic' device in the guest. Maybe your "
                            "libvirt version doesn't support it.")
        try:
            if vm_action == "suspend":
                virsh.suspend(vm_name, ignore_status=False)
            elif vm_action == "resume":
                virsh.suspend(vm_name, ignore_status=False)
                virsh.resume(vm_name, ignore_status=False)
            elif vm_action == "destroy":
                virsh.destroy(vm_name, ignore_status=False)
            elif vm_action == "start":
                virsh.destroy(vm_name, ignore_status=False)
                if start_action == "rename":
                    # rename the guest image file to make guest fail to start
                    os.rename(image_source, new_image_source)
                    virsh.start(vm_name, ignore_status=True)
                else:
                    virsh.start(vm_name, ignore_status=False)
                    if start_action == "restart_libvirtd":
                        libvirtd.restart()
            elif vm_action == "kill":
                if kill_action == "stop_libvirtd":
                    libvirtd.stop()
                    utils_misc.kill_process_by_pattern(vm_name)
                    libvirtd.restart()
                elif kill_action == "reboot_vm":
                    virsh.reboot(vm_name, ignore_status=False)
                    utils_misc.kill_process_tree(vm.get_pid(), signal.SIGKILL)
                else:
                    utils_misc.kill_process_tree(vm.get_pid(), signal.SIGKILL)
            elif vm_action == "crash":
                session = vm.wait_for_login()
                session.cmd("service kdump stop", ignore_all_errors=True)
                # Enable sysRq
                session.cmd("echo 1 > /proc/sys/kernel/sysrq")
                # Send key ALT-SysRq-c to crash VM, and command will not
                # return as vm crashed, so fail early for 'destroy' and
                # 'preserve' action. For 'restart', 'coredump-restart'
                # and 'coredump-destroy' actions, they all need more time
                # to dump core file or restart OS, so using the default
                # session command timeout(60s)
                try:
                    if vm_oncrash_action in ['destroy', 'preserve']:
                        timeout = 3
                    else:
                        timeout = 60
                    session.cmd("echo c > /proc/sysrq-trigger",
                                timeout=timeout)
                except (ShellTimeoutError, ShellProcessTerminatedError):
                    pass
                session.close()
            elif vm_action == "dump":
                dump_file = dump_path + "*" + vm_name + "-*"
                virsh.dump(vm_name, dump_file, dump_option, ignore_status=False)
        except process.CmdError as detail:
            test.error("Guest prepare action error: %s" % detail)

        if libvirtd_state == "off":
            libvirtd.stop()

        # Timing issue cause test to check domstate before prior action
        # kill gets completed
        if vm_action == "kill":
            time.sleep(2)

        if remote_uri:
            remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
            remote_pwd = params.get("remote_pwd", None)
            remote_user = params.get("remote_user", "root")
            if remote_ip.count("EXAMPLE.COM"):
                test.cancel("Test 'remote' parameters not setup")
            ssh_key.setup_ssh_key(remote_ip, remote_user, remote_pwd)

        result = virsh.domstate(vm_ref, extra, ignore_status=True,
                                debug=True, uri=remote_uri)
        status = result.exit_status
        output = result.stdout.strip()

        # check status_error
        if status_error:
            if not status:
                if libvirtd_state == "off" and libvirt_version.version_compare(5, 6, 0):
                    logging.info("From libvirt version 5.6.0 libvirtd is restarted "
                                 "and command should succeed.")
                else:
                    test.fail("Run successfully with wrong command!")
        else:
            if status or not output:
                test.fail("Run failed with right command")
            if extra.count("reason"):
                if vm_action == "suspend":
                    # If not, will cost long time to destroy vm
                    virsh.destroy(vm_name)
                    if not output.count("user"):
                        test.fail(err_msg % vm_action)
                elif vm_action == "resume":
                    if not output.count("unpaused"):
                        test.fail(err_msg % vm_action)
                elif vm_action == "destroy":
                    if not output.count("destroyed"):
                        test.fail(err_msg % vm_action)
                elif vm_action == "start":
                    if start_action == "rename":
                        if not output.count("shut off (failed)"):
                            test.fail(err_msg % vm_action)
                    else:
                        if not output.count("booted"):
                            test.fail(err_msg % vm_action)
                elif vm_action == "kill":
                    if not output.count("crashed"):
                        test.fail(err_msg % vm_action)
                elif vm_action == "crash":
                    if not check_crash_state(output, vm_oncrash_action,
                                             vm_name, dump_file):
                        test.fail(err_msg % vm_action)
                    # VM will be in preserved state, perform virsh reset
                    # and check VM reboots and domstate reflects running
                    # state from crashed state as bug is observed here
                    if vm_oncrash_action == "preserve" and reset_action:
                        virsh_dargs = {'debug': True, 'ignore_status': True}
                        ret = virsh.reset(vm_name, **virsh_dargs)
                        libvirt.check_exit_status(ret)
                        ret = virsh.domstate(vm_name, extra,
                                             **virsh_dargs).stdout.strip()
                        if "paused (crashed)" not in ret:
                            test.fail("vm fails to change state from crashed"
                                      " to paused after virsh reset")
                        # it will be in paused (crashed) state after reset
                        # and resume is required for the vm to reboot
                        ret = virsh.resume(vm_name, **virsh_dargs)
                        libvirt.check_exit_status(ret)
                        vm.wait_for_login()
                        cmd_output = virsh.domstate(vm_name,
                                                    '--reason').stdout.strip()
                        if "running" not in cmd_output:
                            test.fail("guest state failed to get updated")
                    if vm_oncrash_action in ['coredump-destroy',
                                             'coredump-restart']:
                        if not find_dump_file:
                            test.fail("Core dump file is not created in dump "
                                      "path: %s" % dump_path)
                    # For cover bug 1178652
                    if (vm_oncrash_action == "rename-restart" and
                            check_libvirtd_log == "yes"):
                        libvirtd.restart()
                        if not os.path.exists(libvirtd_log_file):
                            test.fail("Expected VM log file: %s not exists"
                                      % libvirtd_log_file)
                        cmd = ("grep -nr '%s' %s" % (err_msg, libvirtd_log_file))
                        if not process.run(cmd, ignore_status=True, shell=True).exit_status:
                            test.fail("Find error message %s from log file: %s."
                                      % (err_msg, libvirtd_log_file))
                elif vm_action == "dump":
                    if dump_option == "--live":
                        if not output.count("running (unpaused)"):
                            test.fail(err_msg % vm_action)
                    elif dump_option == "--crash":
                        if not output.count("shut off (crashed)"):
                            test.fail(err_msg % vm_action)
            if vm_ref == "remote":
                if not (re.search("running", output) or
                        re.search("blocked", output) or
                        re.search("idle", output)):
                    test.fail("Run failed with right command")
    finally:
        qemu_conf.restore()
        if check_libvirtd_log == "yes":
            libvirtd_conf.restore()
            if os.path.exists(libvirtd_log_file):
                os.remove(libvirtd_log_file)
        libvirtd.restart()
        if vm_action == "start" and start_action == "rename":
            os.rename(new_image_source, image_source)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        backup_xml.sync()
        if os.path.exists(dump_path):
            shutil.rmtree(dump_path)
