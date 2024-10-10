import logging as log

from virttest import utils_misc, utils_package


logging = log.getLogger("avocado." + __name__)


def run(test, params, env):
    """
    Confirms that the output of virt-what-cvm is as expected.

    :params test: The avocado test object
    :params params: Parameters for the test
    :params env: The avocado test environment object
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    expected_cvm = params.get("expected_cvm")
    session = vm.wait_for_login()
    utils_package.package_install("virt-what", session=session)
    _, o = utils_misc.cmd_status_output("virt-what-cvm", session=session)
    if o.strip() != expected_cvm.strip():
        test.fail(
            f"Unexpected value '{o.strip()}' instead of {expected_cvm.strip()}."
            " Note that the command is supported since virt-what-1.25-10."
        )
