import logging as log

from avocado.utils import process


logging = log.getLogger('avocado.' + __name__)


def run_cmd_in_guest(vm, cmd, test, timeout=60):
    """
    Run command in the guest

    :params vm: vm object
    :params cmd: a command needs to be ran
    :params session: the vm's session
    :params status: Virsh cmd status
    :params output: Virsh cmd output
    """
    session = vm.wait_for_login()
    status, output = session.cmd_status_output(cmd, timeout=timeout)
    logging.debug("The '%s' output: %s", cmd, output)
    if not status:
        return output
    if output:
        logging.info("Guest is running securely")
    else:
        test.fail("SE environment not enable in guest")
    session.close()


def run(test, params, env):
    """
    1. The guest image is a secure image and the domain already has been setup
    with the 'launchSecurity' element
    2. Confirm SE is Enabled on host
    3. Confirm the guest is running securely
    """

    cmd = params.get("host_se_cmd")
    if process.system_output(cmd, ignore_status=True, shell=True) == "0":
        test.fail("SE not enabled on host")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    cmd = params.get("guest_se_cmd")
    run_cmd_in_guest(vm, cmd, test)
