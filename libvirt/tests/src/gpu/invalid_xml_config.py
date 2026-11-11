from virttest.libvirt_xml import xcepts
from virttest.virt_vm import VMError

from provider.gpu import gpu_base


def run(test, params, env):
    """
    Verify GPU passthrough is rejected with invalid NUMA or SMMUv3 configurations
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    expected_error = params.get("expected_error")

    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"

    try:
        gpu_test.setup_nvgrace_host_driver()
        error_seen = None
        try:
            test.log.info("TEST_STEP: Configure the VM XML with invalid config")
            gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")
            test.log.info("TEST_STEP: Start the VM")
            vm.start()
        except (xcepts.LibvirtXMLError, VMError) as e:
            error_seen = str(e)
            test.log.info(f"VM correctly rejected: {error_seen}")

        if not error_seen:
            test.fail("VM was defined and started successfully, but should "
                      "have been rejected due to invalid configuration")
        elif expected_error and expected_error.lower() not in error_seen.lower():
            test.fail(f"VM was rejected, but not with the expected error. "
                      f"Expected substring: '{expected_error}'. "
                      f"Got: {error_seen}")
    finally:
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
