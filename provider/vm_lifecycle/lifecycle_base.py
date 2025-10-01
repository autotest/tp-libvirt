"""
Basic VM lifecycle operations.

This module provides basic VM lifecycle operations:
- Shutdown
- Reset
- Reboot

These are the core operations that can be reused across different tests.
"""

from virttest import utils_misc
from virttest import virsh

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
