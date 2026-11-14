import re

from virttest import virsh
from virttest import utils_package

from provider.gpu import gpu_base


def run(test, params, env):
    """
    MIG sanity test in vm with GPU device
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    cuda_test = params.get("cuda_test")
    vm_session = None
    cuda_samples_dir = "/root/cuda-samples"

    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"

    try:
        gpu_test.setup_nvgrace_host_driver()
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login()
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        test.log.info("TEST_STEP: Verify NUMA topology")
        if not utils_package.package_install(["numactl"], vm_session):
            test.error("Unable to install numactl in guest")
        numa_output = vm_session.cmd_output("numactl --hardware")
        test.log.debug(f'numactl output: {numa_output}')
        gpu_numa_nodes = re.findall(r"node (\d+) size", numa_output)
        if len(gpu_numa_nodes) < 8:
            test.fail(f"Expected 8 GPU NUMA nodes: {numa_output}")

        test.log.info("TEST_STEP: Clone and build cuda-samples")
        guest_gpu_pci = gpu_base.get_gpu_pci(vm_session)
        gpu_test.clone_cuda_samples(vm_session)
        vm_session.cmd("dnf install -y cmake gcc-c++", timeout=600)
        vm_session.cmd(f"mkdir -p {cuda_samples_dir}/build")
        vm_session.cmd(f"sed -i 's/add_subdirectory(9_CUDA_Tile)/#add_subdirectory(9_CUDA_Tile)/' {cuda_samples_dir}/cpp/CMakeLists.txt || true")
        vm_session.cmd(f"sed -i 's/add_subdirectory(UnifiedMemoryStreams)/#add_subdirectory(UnifiedMemoryStreams)/' {cuda_samples_dir}/Samples/0_Introduction/CMakeLists.txt || true")

        test.log.info("TEST_STEP: Install the driver")
        gpu_test.install_latest_driver(vm_session, True)
        gpu_test.install_cuda_toolkit(vm_session, True)

        test.log.info("TEST_STEP: Check initial MIG mode status")
        initial_status = vm_session.cmd_output_safe(f"nvidia-smi -i {guest_gpu_pci} --query-gpu=mig.mode.current --format=csv,noheader")
        test.log.debug(f"Initial MIG mode status: {initial_status}")

        mig_enabled = False
        try:
            test.log.info("TEST_STEP: Enable MIG mode")
            vm_session.cmd(f"nvidia-smi -i {guest_gpu_pci} -mig 1")
            mig_enabled = True
            output = vm_session.cmd_output(f"nvidia-smi -i {guest_gpu_pci} --query-gpu=pci.bus_id,mig.mode.current --format=csv,noheader")
            test.log.debug(f"MIG mode after enable: {output}")
            if "Enabled" not in output:
                test.fail("Failed to enable MIG mode")

            test.log.info("TEST_STEP: Query MIG profiles")
            profiles_output = vm_session.cmd_output_safe("nvidia-smi mig -lgip")
            test.log.debug(f"Available MIG profiles: {profiles_output}")
            profile_matches = re.findall(r"\|\s*\d+\s+MIG\s+\S+\s+(\d+)\s+(\d+)/\d+", profiles_output)
            available_profile_ids = [pid for pid, free in profile_matches if int(free) > 0]
            if not available_profile_ids:
                test.fail("No available MIG GPU instance profiles found")

            test.log.info("TEST_STEP: Create GPU instances ")
            created_any = False
            for profile_id in available_profile_ids[:3]:
                s, o = vm_session.cmd_status_output(f"nvidia-smi mig -cgi {profile_id} -C")
                if s == 0:
                    created_any = True
                else:
                    test.log.debug(f"Could not create instance for profile {profile_id}: {o}")
            if not created_any:
                test.fail("Failed to create any GPU instances")

            res = vm_session.cmd_output_safe("nvidia-smi -L")
            compute_instances = re.findall(r"Device\s+(\d+).*UUID: (.*)\)", res)
            if not compute_instances:
                test.fail("Failed to get compute instances")

            vm_session.cmd(f"cd {cuda_samples_dir}/build && export PATH=$PATH:/usr/local/cuda/bin/ && cmake -DCMAKE_CUDA_ARCHITECTURES=native -DCMAKE_CXX_STANDARD=17 -DCMAKE_CXX_STANDARD_REQUIRED=ON ..", timeout=600)
            vm_session.cmd(f"cd {cuda_samples_dir}/build && make BlackScholes", timeout=1200)

            for dev in compute_instances:
                s, o = vm_session.cmd_status_output(f"CUDA_VISIBLE_DEVICES={dev[1]} {cuda_test}")
                test.log.debug(f"dev: {dev[1]}, output of BlackScholes: {o}")
                if s:
                    test.fail("Failed to run BlackScholes test")
        finally:
            if mig_enabled:

                test.log.info("TEST_STEP: Destroy the compute instances")
                ci_output = vm_session.cmd_output_safe("nvidia-smi mig -lci")
                test.log.debug(f"Compute instances before destroy: {ci_output}")
                ci_pairs = re.findall(r"\|\s*(\d+)\s+(\d+)\s+MIG\s+\S+\s+\d+\s+(\d+)\s", ci_output)
                for gpu_id, gi_id, ci_id in ci_pairs:
                    test.log.debug(f"Destroy compute instance - gi {gi_id}, ci {ci_id}")
                    s, o = vm_session.cmd_status_output(f"nvidia-smi mig -dci -ci {ci_id} -gi {gi_id}")
                    if s != 0:
                        test.log.error(f"Failed to destroy compute instance ci={ci_id} gi={gi_id}: {o}")

                test.log.info("TEST_STEP: Destroy the GPU instances")
                res = vm_session.cmd_output_safe("nvidia-smi mig -lgi | awk '/MIG/ {print $6}'")
                for line in res.splitlines():
                    if line.strip():
                        gi_id = line.strip()
                        test.log.debug(f"Destroy GPU instance - {gi_id}")
                        s, o = vm_session.cmd_status_output(f"nvidia-smi mig -dgi -gi {gi_id}")
                        if s != 0:
                            test.log.error(f"Failed to destroy GPU instance {gi_id}: {o}")

                test.log.info("TEST_STEP: Disable MIG mode")
                s, o = vm_session.cmd_status_output(f"nvidia-smi -i {guest_gpu_pci} -mig 0")
                if s == 0:
                    output = vm_session.cmd_output(f"nvidia-smi -i {guest_gpu_pci} --query-gpu=pci.bus_id,mig.mode.current --format=csv,noheader")
                    test.log.debug(f"MIG mode after disable: {output}")
                    if "Disabled" not in output:
                        test.log.error("Failed to disable MIG mode")
                else:
                    test.log.error(f"Disable MIG mode command failed (status={s}): {o}")

    finally:
        if vm_session:
            vm_session.cmd(f"rm -rf {cuda_samples_dir}", ignore_all_errors=True)
            vm_session.close()
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
