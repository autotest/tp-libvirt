import re
from autotest.client.shared import error
from virttest.utils_misc import kill_process_by_pattern
from virttest import libvirt_vm, remote, virsh, utils_libvirtd


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
    except error.CmdError:
        raise error.TestError("Guest prepare action error!")

    if libvirtd == "off":
        libvirtd_service.stop()

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
        result = virsh.domstate(vm_ref, extra, ignore_status=True)
        status = result.exit_status
        output = result.stdout.strip()

    # recover libvirtd service start
    if libvirtd == "off":
        libvirtd_service.start()

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
        if vm_ref == "remote":
            if not (re.search("running", output)
                    or re.search("blocked", output)
                    or re.search("idle", output)):
                raise error.TestFail("Run failed with right command")
