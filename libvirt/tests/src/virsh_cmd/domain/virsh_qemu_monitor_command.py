import os
import logging
from autotest.client.shared import error, utils
from virttest import virsh, utils_libvirtd


def run(test, params, env):
    """
    Test command: virsh qemu-monitor-command.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vm_ref = params.get("vm_ref", "domname")
    vm_state = params.get("vm_state", "running")
    cmd = params.get("qemu_cmd", "")
    options = params.get("options", "")
    start_vm = "yes" == params.get("start_vm")
    status_error = "yes" == params.get("status_error", "no")
    domuuid = vm.get_uuid()
    domid = ""
    libvirtd_inst = utils_libvirtd.Libvirtd()

    help_info = virsh.help("qemu-monitor-command").stdout.strip()
    if "--pretty" in options:
        if "--pretty" not in help_info:
            raise error.TestNAError("--pretty option is not supported in"
                                    " current version")

    try:
        # Prepare vm state for test
        if vm_state != "shutoff":
            vm.start()
            vm.wait_for_login()
            domid = vm.get_id()
        if vm_state == "paused":
            vm.pause()

        if vm_ref == "domname":
            vm_ref = vm_name
        elif vm_ref == "domid":
            vm_ref = domid
        elif vm_ref == "domuuid":
            vm_ref = domuuid
        elif domid and vm_ref == "hex_id":
            vm_ref = hex(int(domid))

        # Run virsh command
        cmd_result = virsh.qemu_monitor_command(vm_ref, cmd, options,
                                                ignore_status=True,
                                                debug=True)
        output = cmd_result.stdout.strip()
        status = cmd_result.exit_status

        # Check result
        if not libvirtd_inst.is_running():
            raise error.TestFail("Libvirtd is not running after run command.")
        if status_error:
            if not status:
                # Return status is 0 with unknown command
                if "unknown command:" in output:
                    logging.debug("Command failed: %s" % output)
                else:
                    raise error.TestFail("Expect fail, but run successfully.")
            else:
                logging.debug("Command failed as expected.")
        else:
            if status:
                raise error.TestFail("Expect succeed, but run fail.")
    finally:
        # Cleanup
        if not libvirtd_inst.is_running():
            libvirtd_inst.restart()
