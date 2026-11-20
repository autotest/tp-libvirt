import re

from virttest import utils_package
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_vfio

from provider.gpu import gpu_base


def setup_additional_gpu(vmxml, gpu_index, gpu_pci, base_pxb, base_root_port, pcie_root_slot, params):
    """
    Setup additional GPUs with controllers/iommu/hostdev

    :param vmxml: VM XML object
    :param gpu_index: GPU index (0-based)
    :param gpu_pci: GPU PCI address
    :param base_pxb: Base PXB controller index
    :param base_root_port: Base root port index
    :param pcie_root_slot: PCIe root slot
    :param params: Test parameters
    :return: Next available PCIe slot
    """
    pxb_index = base_pxb + (gpu_index * 5)
    root_port_index = pxb_index + 1

    # Create controllers for topology
    if params.get("pxb_ctrl_attrs"):
        pxb_attrs = eval(params.get("pxb_ctrl_attrs") % (str(pxb_index), pcie_root_slot))
        # Override busNr for additional GPUs to avoid conflicts
        if 'target' in pxb_attrs and 'busNr' in pxb_attrs['target']:
            busNr = 241 - (gpu_index * 10)
            pxb_attrs['target']['busNr'] = str(busNr)
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", pxb_attrs))

        root_port_attrs = eval(params.get("pcie_root_port_attrs") % (str(root_port_index), "%0#4x" % pxb_index))
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", root_port_attrs))
        last_index = root_port_index

        # Upstream port
        if params.get("upstream_port_attrs"):
            upstream_index = last_index + 1
            upstream_bus = "%0#4x" % last_index
            upstream_attrs = eval(params.get("upstream_port_attrs") % (upstream_index, upstream_bus))
            vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", upstream_attrs))
            last_index = upstream_index

        # Downstream port
        if params.get("downstream_port_attrs"):
            downstream_index = last_index + 1
            downstream_bus = "%0#4x" % last_index
            downstream_attrs = eval(params.get("downstream_port_attrs") % (downstream_index, downstream_index, downstream_bus))
            vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("controller", downstream_attrs))
            last_index = downstream_index

        # Create iommu for this pxb
        iommu_dict = eval(params.get("iommu_dict") % str(pxb_index))
        if params.get("cmdqv_mode") == "mixed":
            iommu_dict['driver']['cmdqv'] = 'off'

        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("iommu", iommu_dict))
        gpu_bus = "%0#4x" % last_index

    else:
        gpu_bus = "%0#4x" % (base_root_port + gpu_index)

    # Get nodeset from cfg
    nodeset_map = eval(params.get("nodeset_map", "{}"))
    acpi_nodeset = {'nodeset': nodeset_map[gpu_index]}

    # Build hostdev for this GPU
    additional_gpu_dict = {
        'driver': {'driver_attr': {'iommufd': 'yes'}},
        'acpi': acpi_nodeset,
        'mode': 'subsystem', 'type': 'pci',
        'source': {'untyped_address': gpu_base.pci_to_addr(gpu_pci)},
        'managed': 'no',
        'address': {'attrs': {'type': 'pci', 'domain': '0x0000', 'bus': gpu_bus, 'slot': '0x00', 'function': '0x0'}}
    }
    vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("hostdev", additional_gpu_dict))

    return "%0#4x" % (int(pcie_root_slot, 16) + 1)


def run(test, params, env):
    """
    GPU interconnect and stress testing with NCCL and nvbandwidth
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    num_gpus = int(params.get("num_gpus", "1"))

    # NCCL parameters
    nccl_test_name = params.get("nccl_test_name", "all_gather_perf")
    nccl_min_bytes = params.get("nccl_min_bytes", "8")
    nccl_max_bytes = params.get("nccl_max_bytes", "4G")
    nccl_step_factor = params.get("nccl_step_factor", "2")
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

    test.log.info("TEST_SETUP: Check GPU availability")
    host_gpus = gpu_base.get_gpus_info()
    available_gpu_count = len(host_gpus)
    if available_gpu_count < num_gpus:
        test.cancel(f"Test requires {num_gpus} GPU(s) but only {available_gpu_count} available")
    test.log.info(f"Found {available_gpu_count} GPU(s), test requires {num_gpus}")

    gpu_pcis = list(host_gpus.keys())[:num_gpus]
    test.log.info(f"Using GPUs: {gpu_pcis}")

    try:
        test.log.info("TEST_STEP: Bind GPUs on host")
        gpu_base.bind_gpus_to_vfio(gpu_pcis, test)

        test.log.info("TEST_STEP: Configure VM XML")
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")

        if num_gpus > 1:
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            cntl_index = params.get("cntl_index")
            slot = libvirt_pcicontr.get_free_pci_slot(vmxml)

            for i, gpu_pci in enumerate(gpu_pcis[1:], start=1):
                slot = setup_additional_gpu(vmxml, i, gpu_pci, cntl_index["pxb_1"], cntl_index["pcie_root_port_1"], slot, params)

            vmxml.sync()

        test.log.info("TEST_STEP: Start VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=600)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        test.log.info("TEST_STEP: Install NVIDIA driver")
        gpu_test.install_latest_driver(vm_session)

        test.log.info("TEST_STEP: Reboot VM to load NVIDIA kernel modules")
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

        test.log.info(f"TEST_STEP: Verify {num_gpus} GPU(s) in nvidia-smi")
        gpu_test.nvidia_smi_check(vm_session)

        s, o = vm_session.cmd_status_output("nvidia-smi -L")
        gpu_count = len(re.findall(r'^GPU \d+:', o, re.MULTILINE))
        if gpu_count != num_gpus:
            test.fail(f"nvidia-smi shows {gpu_count} GPU(s), expected {num_gpus}")

        for gpu_pci in gpu_pcis:
            libvirt_vfio.check_vfio_pci(gpu_pci, exp_driver="nvgrace_gpu_vfio_pci")

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
        nccl_cmd = f"cd {nccl_dir} && {cuda_env} ./build/{nccl_test_name} -b {nccl_min_bytes} -e {nccl_max_bytes} -f {nccl_step_factor} -g {num_gpus}"
        o = vm_session.cmd_output(nccl_cmd, timeout=nccl_test_timeout)
        test.log.debug(f"NCCL output:\n{o}")

        # Parse NCCL output for bandwidth
        if num_gpus == 1:
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
                    if num_gpus > 1 and bandwidth < min_device_to_device_bandwidth:
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

        if num_gpus > 1:
            gpu_base.rebind_gpus_to_nvidia(gpu_pcis, skip_first=True, test_obj=test)
