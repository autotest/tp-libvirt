import re

from virttest import virsh
from virttest import utils_package

from provider.gpu import gpu_base


def run(test, params, env):
    """
    LLM workload testing with Ollama in guest with GPU passthrough
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    test_prompt = params.get("ollama_test_prompt", "Hello")
    max_retries = int(params.get("max_ollama_create_retries", "3"))
    min_gpu_util = int(params.get("min_gpu_utilization_percent", "50"))
    model_create_timeout = int(params.get("model_create_timeout", "600"))
    inference_timeout = int(params.get("inference_timeout", "300"))
    modelfile_path = "/root/modelfile"
    gpu_monitor_log = "/root/gpu_monitor.log"

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

        test.log.info("TEST_STEP: Install zstd (required for Ollama installer)")
        if not utils_package.package_install(["zstd"], vm_session):
            test.error("Failed to install zstd in guest")

        test.log.info("TEST_STEP: Install NVIDIA driver")
        gpu_test.install_latest_driver(vm_session)

        test.log.info("TEST_STEP: Reboot VM to load NVIDIA kernel modules")
        vm.reboot(timeout=1200)
        vm_session = vm.wait_for_login(timeout=1200)

        gpu_test.nvidia_smi_check(vm_session)

        test.log.info("TEST_STEP: Install Ollama")
        vm_session.cmd("curl -fsSL https://ollama.com/install.sh -o /tmp/ollama_install.sh", timeout=300)
        vm_session.cmd("sh /tmp/ollama_install.sh > /root/ollama_install.log 2>&1", timeout=1200)
        vm_session.cmd("rm -f /tmp/ollama_install.sh")
        test.log.debug(f"Ollama install output: {vm_session.cmd_output('cat /root/ollama_install.log')}")

        test.log.info("TEST_STEP: Create Ollama model")
        modelfile_content = params.get("modelfile_content", "").replace('\\n', '\n')
        vm_session.cmd(f"cat > {modelfile_path} << 'EOF'\n{modelfile_content}\nEOF")

        create_success = False
        for attempt in range(1, max_retries + 1):
            test.log.info(f"Attempt {attempt}/{max_retries}: ollama create test_model")
            s, o = vm_session.cmd_status_output(f"ollama create test_model -f {modelfile_path}", timeout=model_create_timeout)
            if s == 0:
                create_success = True
                test.log.info("Model created successfully")
                break
            else:
                test.log.warn(f"Attempt {attempt} failed (segfault or error): {o}")

        if not create_success:
            test.fail(f"Failed to create model after {max_retries} attempts")

        test.log.info("TEST_STEP: Warmup inference run")
        s, o = vm_session.cmd_status_output("ollama run test_model 'hi' 2>&1", timeout=inference_timeout)
        test.log.debug(f"Warm-up status: {s}")
        test.log.debug(f"Warm-up output: {o}")

        test.log.info("TEST_STEP: Start GPU monitoring")
        vm_session.cmd(f"rm -f {gpu_monitor_log}")
        monitor_cmd = f"while true; do nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader >> {gpu_monitor_log}; sleep 1; done &"
        vm_session.cmd(monitor_cmd)

        test.log.info(f"TEST_STEP: Run inference with prompt: '{test_prompt}'")
        s, o = vm_session.cmd_status_output(f'ollama run test_model "{test_prompt}" 2>&1', timeout=inference_timeout)
        test.log.debug(f"Ollama output: {o}")
        if s != 0:
            test.fail("Ollama inference failed")

        test.log.info("TEST_STEP: Stop GPU monitoring")
        s, o = vm_session.cmd_status_output("pkill -f 'nvidia-smi --query-gpu'")
        if s != 0:
            test.log.warn(f"Failed to stop GPU monitoring process: {o}")

        test.log.info("TEST_STEP: Check GPU utilization log")
        log_content = vm_session.cmd_output(f"cat {gpu_monitor_log}")
        test.log.debug(f"GPU monitoring log:\n{log_content}")

        # Parse utilization percentages
        max_utilization = 0
        for line in log_content.splitlines():
            match = re.match(r'(\d+)\s*%', line.strip())
            if match:
                util = int(match.group(1))
                max_utilization = max(max_utilization, util)

        test.log.info(f"Peak GPU utilization: {max_utilization}%")
        if max_utilization < min_gpu_util:
            test.fail(f"GPU utilization {max_utilization}% below threshold {min_gpu_util}%")

        test.log.info("TEST_STEP: Cleanup test artifacts")
        if vm_session:
            vm_session.cmd("pkill -f 'nvidia-smi --query-gpu'", ignore_all_errors=True)
            vm_session.cmd("ollama rm test_model", ignore_all_errors=True)
            vm_session.cmd(f"rm -rf /root/ollama_install.log {modelfile_path} {gpu_monitor_log}", ignore_all_errors=True)
            vm_session.close()

    finally:
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
