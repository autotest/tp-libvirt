import logging as log

import aexpect

from virttest import virt_vm
from virttest import virsh


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Verify various kernel panic methods

    1.Prepare test environment.
    2.Execute any needed setup commands
    3.Execute kernel panic command
    4.Verify panic was detected
    5.restore environment
    """

    vm_name = params.get("main_vm", "vm1")
    vm = env.get_vm(vm_name)
    if not vm.is_alive():
        vm.start()

    panic_cmd = params.get("panic_cmd", None)
    status = None
    output = None

    # Setup environment
    session = vm.wait_for_login()
    # Subsequent logins should timeout quickly
    vm.LOGIN_WAIT_TIMEOUT = 10

    # run test case
    try:
        logging.info("Sending panic_cmd command: %s" % panic_cmd)
        status, output = session.cmd_status_output(panic_cmd, timeout=5,
                                                   internal_timeout=5)
    except aexpect.ShellTimeoutError:
        pass  # This is expected
    except Exception:
        # This is unexpected
        raise

    try:
        vm.verify_kernel_crash()
        status = 1  # bad
    except virt_vm.VMDeadKernelCrashError:
        status = 0  # good

    # Restore environment to stable state
    session.close()
    vm.serial_console.close()
    virsh.destroy(vm_name)

    # check status_error
    if status:
        test.fail("Panic command failed to cause panic")
