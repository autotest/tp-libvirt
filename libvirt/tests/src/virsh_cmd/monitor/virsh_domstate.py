import re
import os
import shutil
import logging
from autotest.client.shared import error
from virttest.utils_misc import kill_process_by_pattern
from autotest.client.shared import utils
from virttest import libvirt_vm
from virttest import remote
from virttest import virsh
from virttest import utils_libvirtd
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.panic import Panic
from virttest.aexpect import ShellTimeoutError

QEMU_CONF = "/etc/libvirt/qemu.conf"
QEMU_CONF_BK = "/etc/libvirt/qemu.conf.bk"


class ActionError(Exception):

    """
    Error in vm action.
    """

    def __init__(self, action):
        Exception.__init__(self)
        self.action = action

    def __str__(self):
        return str("Get wrong info when guest action"
                   " is %s" % self.action)


def check_crash_state(cmd_output, crash_action, dump_file=""):
    """
    Check the domain state about crash actions.
    """
    expect_output = ""
    crash_state = False
    find_dump_file = False

    if crash_action in ["destroy", "coredump-destroy"]:
        expect_output = "shut off (crashed)"
    elif crash_action in ["restart", "coredump-restart"]:
        expect_output = "running (crashed)"
    elif crash_action == "preserve":
        expect_output = "crashed (panicked)"

    if cmd_output.strip() == expect_output:
        crash_state = True
    if dump_file:
        result = utils.run("ls %s" % dump_file, ignore_status=True)
        if result.exit_status == 0:
            logging.debug("Find the auto dump core file:\n%s", result.stdout)
            find_dump_file = True
        return crash_state and find_dump_file
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
    vm_name = params.get("main_vm", "virt-tests-vm1")
    vm = env.get_vm(vm_name)

    libvirtd = params.get("libvirtd", "on")
    vm_ref = params.get("domstate_vm_ref")
    status_error = (params.get("status_error", "no") == "yes")
    extra = params.get("domstate_extra", "")
    vm_action = params.get("domstate_vm_action", "")
    vm_oncrash_action = params.get("domstate_vm_oncrash")

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
    utils.run("cp %s %s" % (QEMU_CONF, QEMU_CONF_BK))

    dump_path = os.path.join(test.tmpdir, "dump/")
    dump_file = ""
    if vm_action == "crash":
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Set on_crash action
        vmxml.on_crash = vm_oncrash_action
        # Add <panic> device to domain
        panic_dev = Panic()
        panic_dev.addr_type = "isa"
        panic_dev.addr_iobase = "0x505"
        vmxml.add_device(panic_dev)
        vmxml.sync()
        # Config auto_dump_path in qemu.conf
        cmd = "echo auto_dump_path = \\\"%s\\\" >> %s" % (dump_path, QEMU_CONF)
        utils.run(cmd)
        libvirtd_service.restart()
        if vm_oncrash_action in ['coredump-destroy', 'coredump-restart']:
            dump_file = dump_path + vm_name + "-*"
        # Start VM and check the panic device
        virsh.start(vm_name, ignore_status=False)
        vmxml_new = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Skip this test if no panic device find
        if not vmxml_new.xmltreefile.find('devices').findall('panic'):
            raise error.TestNAError("No 'panic' device in the guest, maybe "
                                    "your libvirt version doesn't support it")
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
            kill_process_by_pattern(vm_name)
            libvirtd_service.restart()
        elif vm_action == "crash":
            session = vm.wait_for_login()
            # Stop kdump in the guest
            session.cmd("service kdump stop", ignore_all_errors=True)
            # Enable sysRq
            session.cmd("echo 1 > /proc/sys/kernel/sysrq")
            # Send key ALT-SysRq-c to crash VM, and command will not return
            # as vm crashed, so fail early for 'destroy' and 'preserve' action.
            # For 'restart', 'coredump-restart' and 'coredump-destroy' actions,
            # they all need more time to dump core file or restart OS, so using
            # the default session command timeout(60s)
            try:
                if vm_oncrash_action in ['destroy', 'preserve']:
                    timeout = 3
                else:
                    timeout = 60
                session.cmd("echo c > /proc/sysrq-trigger", timeout=timeout)
            except ShellTimeoutError:
                pass
            session.close()
    except error.CmdError, e:
        raise error.TestError("Guest prepare action error: %s" % e)

    if libvirtd == "off":
        libvirtd_service.stop()

    try:
        if vm_ref == "remote":
            remote_ip = params.get("remote_ip", "REMOTE.EXAMPLE.COM")
            local_ip = params.get("local_ip", "LOCAL.EXAMPLE.COM")
            remote_pwd = params.get("remote_pwd", None)
            if remote_ip.count("EXAMPLE.COM") or local_ip.count("EXAMPLE.COM"):
                raise error.TestNAError("Test 'remote' parameters not setup")
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
            except error.CmdError:
                status = 1
        else:
            result = virsh.domstate(vm_ref, extra, ignore_status=True,
                                    debug=True)
            status = result.exit_status
            output = result.stdout.strip()

        # check status_error
        if status_error:
            if not status:
                raise error.TestFail("Run successfully with wrong command!")
        else:
            if status or not output:
                raise error.TestFail("Run failed with right command")
            if extra.count("reason"):
                if vm_action == "suspend":
                    # If not, will cost long time to destroy vm
                    virsh.destroy(vm_name)
                    if not output.count("user"):
                        raise ActionError(vm_action)
                elif vm_action == "resume":
                    if not output.count("unpaused"):
                        raise ActionError(vm_action)
                elif vm_action == "destroy":
                    if not output.count("destroyed"):
                        raise ActionError(vm_action)
                elif vm_action == "start":
                    if not output.count("booted"):
                        raise ActionError(vm_action)
                elif vm_action == "kill":
                    if not output.count("crashed"):
                        raise ActionError(vm_action)
                elif vm_action == "crash":
                    if not check_crash_state(output, vm_oncrash_action, dump_file):
                        raise ActionError(vm_action)
            if vm_ref == "remote":
                if not (re.search("running", output)
                        or re.search("blocked", output)
                        or re.search("idle", output)):
                    raise error.TestFail("Run failed with right command")
    finally:
        # recover libvirtd service start
        if libvirtd == "off":
            utils_libvirtd.libvirtd_start()
        # Recover VM
        vm.destroy(gracefully=False)
        backup_xml.sync()
        utils.run("mv -f %s %s" % (QEMU_CONF_BK, QEMU_CONF))
        if os.path.exists(dump_path):
            shutil.rmtree(dump_path)
