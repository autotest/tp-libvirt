import re

from virttest import virsh
from virttest import utils_package

from provider.gpu import gpu_base


def run(test, params, env):
    """
    GPU interconnect and stress testing with NCCL and nvbandwidth
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    # NCCL parameters
    nccl_test_name = params.get("nccl_test_name", "all_gather_perf")
    nccl_min_bytes = params.get("nccl_min_bytes", "8")
    nccl_max_bytes = params.get("nccl_max_bytes", "4G")
    nccl_step_factor = params.get("nccl_step_factor", "2")
    nccl_num_gpus = int(params.get("nccl_num_gpus", "1"))
    nccl_test_timeout = int(params.get("nccl_test_timeout", "600"))
    min_nccl_bandwidth = float(params.get("min_nccl_bandwidth", "10"))

    # nvbandwidth parameters
    nvbandwidth_tests = eval(params.get("nvbandwidth_tests", "[]"))
    nvbandwidth_test_timeout = int(params.get("nvbandwidth_test_timeout", "300"))
    min_host_to_device_bandwidth = float(params.get("min_host_to_device_bandwidth", "100"))
    min_device_to_host_bandwidth = float(params.get("min_device_to_host_bandwidth", "100"))
    min_device_to_device_bandwidth = float(params.get("min_device_to_device_bandwidth", "700"))

    git_clone_timeout = int(params.get("git_clone_timeout", "600"))
    build_timeout = int(params.get("build_timeout", "600"))
    nccl_dir = "/root/nccl-tests"
    nvbw_dir = "/root/nvbandwidth"
    cuda_env = "PATH=/usr/local/cuda/bin:${PATH} LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH}"

    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"

    try:
        test.log.info("TEST_STEP: Bind GPU on host")
        gpu_test.setup_nvgrace_host_driver()

        test.log.info("TEST_STEP: Configure VM XML with GPU")
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=600)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        test.log.info("TEST_STEP: Install NVIDIA driver")
        gpu_test.install_latest_driver(vm_session)

        test.log.info("TEST_STEP: Reboot VM to load NVIDIA kernel modules")
        vm.reboot(timeout=1200)
        vm_session = vm.wait_for_login(timeout=1200)

        gpu_test.nvidia_smi_check(vm_session)

        test.log.info("TEST_STEP: Install CUDA toolkit")
        gpu_test.install_cuda_toolkit(vm_session)

        test.log.info("TEST_STEP: Install NCCL libraries")
        if not utils_package.package_install(["libnccl", "libnccl-devel"], vm_session, timeout=600):
            test.error("Failed to install NCCL libraries")

        s, o = vm_session.cmd_status_output("/sbin/ldconfig -p | grep nccl")
        if s != 0:
            test.error("NCCL libraries not found after installation")
        test.log.debug(f"NCCL libraries: {o}")

        test.log.info("TEST_STEP: Clone nccl-tests repository")
        vm_session.cmd("cd /root && git clone https://github.com/NVIDIA/nccl-tests.git", timeout=git_clone_timeout)
        test.log.info("TEST_STEP: Build nccl-tests")
        build_cmd = f"cd {nccl_dir} && {cuda_env} make CUDA_HOME=/usr/local/cuda NCCL_HOME=/usr -j$(nproc)"
        vm_session.cmd(build_cmd, timeout=build_timeout)

        test.log.info(f"TEST_STEP: Run NCCL {nccl_test_name} test")
        nccl_cmd = f"cd {nccl_dir} && {cuda_env} ./build/{nccl_test_name} -b {nccl_min_bytes} -e {nccl_max_bytes} -f {nccl_step_factor} -g {nccl_num_gpus}"
        o = vm_session.cmd_output(nccl_cmd, timeout=nccl_test_timeout)
        test.log.debug(f"NCCL output:\n{o}")

        # Parse NCCL output for bandwidth
        if nccl_num_gpus == 1:
            # For single GPU, skip bandwidth check (bus bandwidth is always 0)
            if "Out of bounds values : 0 OK" not in o:
                test.fail("NCCL test did not report 'Out of bounds values : 0 OK'")
            else:
                test.log.info("NCCL test passed (single GPU, no bus bandwidth)")
        else:
            # For multi-GPU, check bus bandwidth
            if "Out of bounds values : 0 OK" not in o:
                test.log.warn("NCCL test did not report 'Out of bounds values : 0 OK'")
            avg_bw_match = re.search(r'Avg bus bandwidth\s*:\s*([\d.]+)', o)
            if avg_bw_match:
                avg_bandwidth = float(avg_bw_match.group(1))
                test.log.info(f"NCCL average bus bandwidth: {avg_bandwidth} GB/s")
                if avg_bandwidth < min_nccl_bandwidth:
                    test.fail(f"NCCL bandwidth {avg_bandwidth} GB/s below the threshold {min_nccl_bandwidth} GB/s")

        test.log.info("TEST_STEP: Install nvbandwidth dependencies")
        if not utils_package.package_install(["cmake", "boost-devel", "gcc-c++"], vm_session, timeout=600):
            test.error("Failed to install nvbandwidth dependencies")

        test.log.info("TEST_STEP: Clone nvbandwidth repository")
        vm_session.cmd("cd /root && git clone https://github.com/NVIDIA/nvbandwidth.git", timeout=git_clone_timeout)

        test.log.info("TEST_STEP: Build nvbandwidth")
        vm_session.cmd(f"cd {nvbw_dir} && mkdir -p build && cd build && {cuda_env} cmake ..", timeout=build_timeout)
        vm_session.cmd(f"cd {nvbw_dir}/build && {cuda_env} make -j$(nproc)", timeout=build_timeout)

        for test_type in nvbandwidth_tests:
            test.log.info(f"TEST_STEP: Run nvbandwidth {test_type}")
            nvbw_cmd = f"cd {nvbw_dir}/build && {cuda_env} ./nvbandwidth -t {test_type}"
            s, o = vm_session.cmd_status_output(nvbw_cmd, timeout=nvbandwidth_test_timeout)
            test.log.debug(f"nvbandwidth {test_type} output:\n{o}")
            if s != 0:
                test.fail(f"nvbandwidth {test_type} failed with status {s}")

            # Parse bandwidth from output
            if "host_to_device" in test_type:
                sum_match = re.search(rf'SUM {test_type}\s+([\d.]+)', o)
                if sum_match:
                    bandwidth = float(sum_match.group(1))
                    test.log.info(f"{test_type} total bandwidth: {bandwidth} GB/s")
                    if bandwidth < min_host_to_device_bandwidth:
                        test.fail(f"{test_type} bandwidth {bandwidth} GB/s below threshold {min_host_to_device_bandwidth} GB/s")
                else:
                    test.log.warn(f"Could not parse bandwidth from {test_type} output")

            elif "device_to_host" in test_type:
                sum_match = re.search(rf'SUM {test_type}\s+([\d.]+)', o)
                if sum_match:
                    bandwidth = float(sum_match.group(1))
                    test.log.info(f"{test_type} total bandwidth: {bandwidth} GB/s")
                    if bandwidth < min_device_to_host_bandwidth:
                        test.fail(f"{test_type} bandwidth {bandwidth} GB/s below threshold {min_device_to_host_bandwidth} GB/s")
                else:
                    test.log.warn(f"Could not parse bandwidth from {test_type} output")

            elif "device_to_device" in test_type:
                sum_match = re.search(rf'SUM {test_type}\s+([\d.]+)', o)
                if sum_match:
                    bandwidth = float(sum_match.group(1))
                    test.log.info(f"{test_type} total bandwidth: {bandwidth} GB/s")
                    if nccl_num_gpus > 1 and bandwidth < min_device_to_device_bandwidth:
                        test.fail(f"{test_type} bandwidth {bandwidth} GB/s below threshold {min_device_to_device_bandwidth} GB/s")
                else:
                    test.log.info(f"{test_type} reported N/A (expected for single GPU or disabled P2P)")

        test.log.info("TEST_STEP: Cleanup test artifacts")
        if vm_session:
            vm_session.cmd(f"rm -rf {nccl_dir} {nvbw_dir}", ignore_all_errors=True)
            vm_session.close()

    finally:
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
