import re
import os
import shutil
import logging
import platform

from aexpect import ShellTimeoutError
from aexpect import ShellProcessTerminatedError

from avocado.utils import process

from virttest import libvirt_vm
from virttest import remote
from virttest import virsh
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_config
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.panic import Panic

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
    elif crash_action == "preserve":
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

    logging.debug("Actual state: %s", cmd_output.strip())
    if cmd_output.strip() == expect_output:
        crash_state = True
    if dump_file:
        result = process.run("ls %s" % dump_file,
                             ignore_status=True, shell=True)
        if result.exit_status == 0:
            logging.debug("Find the auto dump core file:\n%s", result.stdout)
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

    domid = vm.get_id()
    domuuid = vm.get_uuid()
    libvirtd_service = utils_libvirtd.Libvirtd()

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

    dump_path = os.path.join(test.tmpdir, "dump/")
    logging.debug("dump_path: %s", dump_path)
    os.mkdir(dump_path)
    dump_file = ""
    try:
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
            libvirtd_service.restart()
            if vm_oncrash_action in ['coredump-destroy', 'coredump-restart']:
                dump_file = dump_path + "*" + vm_name + "-*"
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
                virsh.start(vm_name, ignore_status=False)
            elif vm_action == "kill":
                libvirtd_service.stop()
                utils_misc.kill_process_by_pattern(vm_name)
                libvirtd_service.restart()
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
        except process.CmdError, detail:
            test.error("Guest prepare action error: %s" % detail)

        if libvirtd_state == "off":
            libvirtd_service.stop()

        if vm_ref == "remote":
            remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
            local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
            remote_pwd = params.get("remote_pwd", None)
            if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
                test.cancel("Test 'remote' parameters not setup")
            status = 0
            try:
                remote_uri = libvirt_vm.complete_uri(local_ip)
                session = remote.remote_login("ssh", remote_ip, "22", "root",
                                              remote_pwd, "#")
                session.cmd_output('LANG=C')
                command = "virsh -c %s domstate %s" % (remote_uri, vm_name)
                status, output = session.cmd_status_output(command,
                                                           internal_timeout=5)
                session.close()
            except process.CmdError:
                status = 1
        else:
            result = virsh.domstate(vm_ref, extra, ignore_status=True,
                                    debug=True)
            status = result.exit_status
            output = result.stdout.strip()

        # check status_error
        if status_error:
            if not status:
                test.fail("Run successfully with wrong command!")
        else:
            if status or not output:
                test.fail("Run failed with right command")
            if extra.count("reason"):
                err_msg = ("virsh domstate for action %s doesn't return state "
                           "as expected" % vm_action)
                if vm_action == "suspend":
                    # If not, will cost long time to destroy vm
                    virsh.destroy(vm_name)
                    if not output.count("user"):
                        test.fail(err_msg)
                elif vm_action == "resume":
                    if not output.count("unpaused"):
                        test.fail(err_msg)
                elif vm_action == "destroy":
                    if not output.count("destroyed"):
                        test.fail(err_msg)
                elif vm_action == "start":
                    if not output.count("booted"):
                        test.fail(err_msg)
                elif vm_action == "kill":
                    if not output.count("crashed"):
                        test.fail(err_msg)
                elif vm_action == "crash":
                    if not check_crash_state(output, vm_oncrash_action,
                                             vm_name, dump_file):
                        test.fail(err_msg)
                    # VM will be in preserved state, perform virsh reset
                    # and check VM reboots and domstate reflects running
                    # state from crashed state as bug is observed here
                    if vm_oncrash_action == "preserve" and reset_action:
                        virsh_dargs = {'debug': True, 'ignore_status': True}
                        ret = virsh.reset(vm_name, **virsh_dargs)
                        if ret.exit_status != 0:
                            test.fail("virsh reset failed after guest crash: "
                                      "%s" % ret.stderr)
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
            if vm_ref == "remote":
                if not (re.search("running", output) or
                        re.search("blocked", output) or
                        re.search("idle", output)):
                    test.fail("Run failed with right command")
    finally:
        qemu_conf.restore()
        libvirtd.restart()
        vm.destroy(gracefully=False)
        backup_xml.sync()
        if os.path.exists(dump_path):
            shutil.rmtree(dump_path)
