import logging
import time
from virttest import virsh
from virttest import utils_misc


def power_cycle_vm(test, vm, vm_name, login_timeout, startup_wait, resume_wait):
    """
    Cycle vm through start-login-shutdown loop

    :param vm: vm object
    :param vm_name: vm name
    :param login_timeout: timeout given to vm.wait_for_login
    :param startup_wait: how long to wait after the vm has been started
    :param resume_wait: how long to wait after the paused vm is resumed

    This tests the vm startup and destroy sequences by:
        1) Starting the vm
        2) Verifying the vm is alive
        3) Logging into vm
        4) Logging out of the vm
        5) Destroying the vm
    """

    virsh.start(vm_name, options="--paused", ignore_status=False)
    time.sleep(startup_wait)

    virsh.resume(vm_name, ignore_status=False)
    time.sleep(resume_wait)

    session = vm.wait_for_login(timeout=login_timeout)
    session.close()

    virsh.shutdown(vm_name, ignore_status=False)

    utils_misc.wait_for(lambda: vm.state() == "shut off", 360)
    if vm.state() != "shut off":
        test.fail("Failed to shutdown VM")


def run(test, params, env):
    """
    Test qemu-kvm startup reliability

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    num_cycles = int(params.get("num_cycles"))                  # Parameter to control the number of times to start/restart the vm
    login_timeout = float(params.get("login_timeout", 240))     # Controls vm.wait_for_login() timeout
    startup_wait = float(params.get("startup_wait", 2))         # Controls wait time for virsh.start()
    resume_wait = float(params.get("resume_wait", 40))          # Controls wait for virsh.resume()

    for i in range(num_cycles):
        logging.info("Starting vm '%s' -- attempt #%d", vm_name, i+1)
        power_cycle_vm(test, vm, vm_name, login_timeout, startup_wait, resume_wait)
        logging.info("\t-> Completed vm '%s' power cycle #%d", vm_name, i+1)
