import re

from virttest import virsh
from virttest import utils_package

from provider.gpu import gpu_base


def run(test, params, env):
    """
    Test CUDA functionality with GPU passthrough
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    cuda_tests = eval(params.get("cuda_tests", "[]"))
    max_cuda_failures = int(params.get("max_cuda_failures", "0"))
    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"
    cuda_samples_dir = "/root/cuda-samples"

    try:
        gpu_test.setup_nvgrace_host_driver()

        test.log.info("TEST_STEP: Configure the VM XML")
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=600)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        test.log.info("TEST_STEP: Clone cuda-samples")
        gpu_test.clone_cuda_samples(vm_session)
        test.log.info("TEST_STEP: Install the driver")
        vm_session.cmd("dnf clean metadata", timeout=60)
        gpu_test.install_latest_driver(vm_session)

        test.log.info("TEST_STEP: Reboot VM to load new NVIDIA kernel modules")
        vm.reboot(timeout=1200)
        vm_session = vm.wait_for_login(timeout=1200)

        test.log.info("TEST_STEP: Load nvidia modules")
        s, o = vm_session.cmd_status_output("modprobe nvidia-uvm || true")
        test.log.debug(f"modprobe nvidia-uvm: status={s}, output={o}")
        s, o = vm_session.cmd_status_output("nvidia-modprobe -u -c 0 || true")
        test.log.debug(f"nvidia-modprobe: status={s}, output={o}")
        s, o = vm_session.cmd_status_output("modprobe nvidia")
        test.log.info(f"modprobe nvidia: status={s}, output={o}")

        s, o = vm_session.cmd_status_output("systemctl start nvidia-persistenced || true")
        test.log.info(f"start nvidia-persistenced: status={s}, output={o}")

        vm_session.cmd("sleep 30")
        test.log.info("Waited 30s for nvidia-persistenced to initialize NUMA nodes")

        s, o = vm_session.cmd_status_output("dmesg | grep -i 'NUMA was not set up'")
        if o:
            test.log.warn(f"NUMA setup warning found: {o}")

        if not utils_package.package_install(["numactl"], vm_session):
            test.error("Unable to install numactl in guest")
        s, o = vm_session.cmd_status_output("numactl --hardware")
        test.log.info(f"NUMA nodes: {o}")

        gpu_test.install_cuda_toolkit(vm_session)
        gpu_test.nvidia_smi_check(vm_session)

        test.log.info("TEST_STEP: Build CUDA samples")
        vm_session.cmd("dnf install -y git cmake mesa-libGL-devel freeglut-devel vulkan-headers vulkan-loader-devel mesa-vulkan-drivers glfw-devel gcc-c++", timeout=600)
        vm_session.cmd(f"rm -rf {cuda_samples_dir}/build")
        vm_session.cmd(f"mkdir {cuda_samples_dir}/build")
        vm_session.cmd(f"sed -i 's/add_subdirectory(9_CUDA_Tile)/#add_subdirectory(9_CUDA_Tile)/' {cuda_samples_dir}/cpp/CMakeLists.txt || true")
        vm_session.cmd(f"sed -i 's/add_subdirectory(UnifiedMemoryStreams)/#add_subdirectory(UnifiedMemoryStreams)/' {cuda_samples_dir}/Samples/0_Introduction/CMakeLists.txt || true")
        vm_session.cmd(f"cd {cuda_samples_dir}/build && export PATH=$PATH:/usr/local/cuda/bin/ && cmake -DCMAKE_CUDA_ARCHITECTURES=native -DCMAKE_CXX_STANDARD=17 -DCMAKE_CXX_STANDARD_REQUIRED=ON ..", timeout=600)
        vm_session.cmd(f"cd {cuda_samples_dir}/build && make -j 64", timeout=1200)

        test.log.info("TEST_STEP: Run cuda sanity tests")
        if cuda_tests:
            # select_tests
            for s_test in cuda_tests:
                test.log.debug(f"TEST_STEP: Run {s_test}")
                s, o = vm_session.cmd_status_output(f"LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH {s_test}", timeout=600)
                test.log.debug(f"output: {o}")
                if s:
                    test.fail("Failed to run cuda sanity - %s" % s_test)

        else:
            # all_tests
            s, o = vm_session.cmd_status_output(f"cd {cuda_samples_dir} && export PATH=$PATH:/usr/local/cuda/bin/ && export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH && python3 run_tests.py --dir ./build/cpp --output results", timeout=2000)
            test.log.info(f"run_tests.py output: {o}")

            if "Test Summary:" not in o:
                test.fail("run_tests.py did not complete properly - no Test Summary found")

            failed_match = re.search(r'Failed runs \((\d+)\)', o)
            failed_count = int(failed_match.group(1)) if failed_match else 0
            if failed_count > max_cuda_failures:
                test.fail(f"CUDA tests FAILED: {failed_count} failures exceeds threshold of {max_cuda_failures}")
            else:
                test.log.info(f"CUDA tests PASSED: {failed_count} failures within acceptable threshold of {max_cuda_failures}")

        test.log.info("TEST_STEP: Cleanup test artifacts")
        if vm_session:
            vm_session.cmd(f"rm -rf {cuda_samples_dir}", ignore_all_errors=True)
            vm_session.close()

    finally:
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
