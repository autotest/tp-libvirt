"""
Base module for VM lifecycle operations.

This module provides common methods for VM lifecycle operations such as:
- Shutdown
- Reset
- Reboot
- Suspend/Resume
- Save/Restore

These methods handle serial console setup/teardown and network connectivity checks.
"""

import os

from virttest import utils_misc
from virttest import virsh

from provider.save import save_base

# Common virsh arguments
VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def test_shutdown(test, vm, params):
    """
    Test shutdown scenario

    :param test: Test object
    :param vm: VM object
    :param params: Dictionary with the test parameters
    :raises: TestFail if VM fails to shutdown
    """
    test.log.info("TEST_STEP: Shutdown the VM.")
    virsh.shutdown(vm.name, **VIRSH_ARGS)
    shutdown_timeout = int(params.get("shutdown_timeout", "60"))
    if not utils_misc.wait_for(lambda: vm.is_dead(), shutdown_timeout):
        test.fail("VM failed to shutdown")
    test.log.info("VM successfully shutdown")


def test_reset(test, vm, login_timeout):
    """
    Test reset scenario

    :param test: Test object
    :param vm: VM object
    :param login_timeout: Login timeout
    """
    test.log.info("TEST_STEP: Reset the VM.")
    session = vm.wait_for_serial_login(timeout=login_timeout)
    virsh.reset(vm.name, **VIRSH_ARGS)
    _match, _text = session.read_until_last_line_matches(
            [r"[Ll]ogin:\s*"], timeout=login_timeout, internal_timeout=0.5)
    session.close()


def test_reboot(test, vm, params, login_timeout):
    """
    Test single reboot operation

    :param test: Test object
    :param vm: VM object
    :param params: Dictionary with the test parameters
    :param login_timeout: Login timeout
    """
    test.log.info("TEST_STEP: Reboot the VM.")
    session = vm.wait_for_serial_login(timeout=login_timeout)
    session.sendline(params.get("reboot_command"))
    _match, _text = session.read_until_last_line_matches(
        [r"[Ll]ogin:\s*"], timeout=login_timeout, internal_timeout=0.5)
    session.close()


def test_suspend_resume(test, vm, params):
    """
    Test suspend/resume scenario

    :param test: Test object
    :param vm: VM object
    :param params: Dictionary with the test parameters
    """
    test.log.info("TEST_STEP: Suspend and resume the VM.")
    virsh.suspend(vm.name, **VIRSH_ARGS)
    virsh.resume(vm.name, **VIRSH_ARGS)


def test_save_restore(test, vm, params, save_path):
    """
    Test save/restore scenario

    :param test: Test object
    :param vm: VM object
    :param params: Dictionary with the test parameters
    :param save_path: Path to save the VM state
    """
    test.log.info("TEST_STEP: Save and restore the VM.")
    virsh.save(vm.name, save_path, **VIRSH_ARGS)
    virsh.restore(save_path, **VIRSH_ARGS)


def test_lifecycle_operation(test, vm, params, test_scenario=None, network_check_callback=None):
    """
    Test VM lifecycle operations (reboot, reset, shutdown, save/restore, suspend/resume)

    :param test: Test object
    :param vm: VM object
    :param params: Dictionary with the test parameters
    :param test_scenario: The test scenario to run
    :param network_check_callback: Callback function to check network connectivity
    """
    # Common parameters
    login_timeout = int(params.get('login_timeout', 240))
    rand_id = utils_misc.generate_random_string(3)
    save_path = f'/var/tmp/{vm.name}_{rand_id}.save'

    try:
        # Setup serial console
        vm.cleanup_serial_console()
        vm.create_serial_console()

        # Execute the appropriate test scenario
        if test_scenario == "shutdown":
            test_shutdown(test, vm, params)
            if params.get("start_after_shutdown", "no") == "yes":
                test.log.info("TEST_STEP: Starting VM after shutdown")
                vm.start()
            else:
                return
        elif test_scenario == "reset":
            test_reset(test, vm, login_timeout)
        elif test_scenario == "reboot_many_times":
            for _ in range(int(params.get('loop_time', '5'))):
                test_reset(test, vm, login_timeout)
        elif test_scenario == "save_restore":
            pid_ping, upsince = save_base.pre_save_setup(vm, serial=True)
            test_save_restore(test, vm, params, save_path)
            save_base.post_save_check(vm, pid_ping, upsince, serial=True)
            return
        elif test_scenario == "suspend_resume":
            pid_ping, upsince = save_base.pre_save_setup(vm, serial=True)
            test_suspend_resume(test, vm, params)
            save_base.post_save_check(vm, pid_ping, upsince, serial=True)
            return
        else:
            test.log.warning(f"Unknown scenario: {test_scenario}")
            return

        # Final login to check network access
        if network_check_callback:
            session = vm.wait_for_serial_login(timeout=login_timeout)
            network_check_callback(session)
            session.close()

    finally:
        # Cleanup save file if it exists
        if os.path.exists(save_path):
            os.remove(save_path)
