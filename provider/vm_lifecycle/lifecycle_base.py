"""
Reusable VM lifecycle operations.

This module provides:
- Shutdown with wait and failure assertion
"""

from virttest import virsh

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
    if not vm.wait_for_shutdown(count=shutdown_timeout):
        test.fail("VM failed to shutdown")
    test.log.info("VM successfully shutdown")
