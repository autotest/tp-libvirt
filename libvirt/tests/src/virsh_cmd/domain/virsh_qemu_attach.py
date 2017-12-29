import re
import logging

from virttest import virsh
from virttest import utils_libvirtd
from virttest import qemu_vm


def run(test, params, env):
    """
    Test command: virsh qemu-attach.
    """
    pid = params.get("pid", None)
    options = params.get("options", "")
    status_error = "yes" == params.get("status_error", "no")
    libvirtd_inst = utils_libvirtd.Libvirtd()
    new_vm = None

    try:
        # Prepare qemu-kvm process
        if pid is None:
            params_b = params.copy()
            new_vm = qemu_vm.VM('attach_dom', params_b, test.bindir,
                                env['address_cache'])
            new_vm.create()
            pid = new_vm.get_pid()

        # Run virsh command
        logging.debug("The qemu-kvm pid for attach is %s" % pid)
        cmd_result = virsh.qemu_attach(pid, options,
                                       ignore_status=True,
                                       debug=True)
        status = cmd_result.exit_status

        # Check result
        if not libvirtd_inst.is_running():
            test.fail("Libvirtd is not running after run command.")
        if status_error:
            if not status:
                test.fail("Expect fail, run succeed.")
            else:
                logging.debug("Command failed as expected.")
        else:
            if status:
                err_msg = "error: Failed to attach to pid"
                if err_msg in cmd_result.stderr:
                    test.fail("Command failed: %s" % cmd_result.stderr)
                list_output = virsh.dom_list().stdout.strip()
                if re.search('attach_dom', list_output):
                    test.fail("Command failed but domain found "
                              "in virsh list.")
                err_msg = "No worry, the command is explicitly unsupported, "
                err_msg += "it's a development crutch and not highly reliable"
                err_msg += " mechanism."
                test.cancel(err_msg)
    finally:
        # Cleanup
        if new_vm:
            if new_vm.is_alive():
                new_vm.destroy(gracefully=False)
        if not libvirtd_inst.is_running():
            libvirtd_inst.restart()
