import os

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.gpu import gpu_base


def run(test, params, env):
    """
    cuda sanity tests in guest
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    cuda_tests = eval(params.get("cuda_tests", "[]"))
    gpu_test = gpu_base.GPUTest(vm, test, params)
    hostdev_dict = gpu_test.parse_hostdev_dict()

    try:
        gpu_test.setup_default()
        libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name), "hostdev",
                hostdev_dict)
        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login()
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        cuda_samples_path = params.get("cuda_samples_path")
        cuda_sample_guest_path = os.path.join("/tmp", os.path.basename(cuda_samples_path))
        vm_session.cmd(f"wget {cuda_samples_path} -O {cuda_sample_guest_path}")
        vm_session.cmd(f"tar -xf {cuda_sample_guest_path} -C /root")
        vm_session.cmd(f"rm -rf {cuda_sample_guest_path}")

        test.log.info("TEST_STEP: Install the driver")
        gpu_test.install_latest_driver(vm_session)
        gpu_test.install_cuda_toolkit(vm_session)
        gpu_test.nvidia_smi_check(vm_session)

        test.log.info("TEST_STEP: Run cuda sanity tests")
        for s_test in cuda_tests:
            test.log.debug(f"TEST_STEP: Run {s_test}")
            s, o = vm_session.cmd_status_output(s_test, timeout=600)
            test.log.debug(f"output: {o}")
            if s:
                test.fail("Failed to run cuda sanity - %s" % s_test)

    finally:
        gpu_test.teardown_default()
