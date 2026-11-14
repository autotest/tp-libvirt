from virttest import virsh
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml

from provider.gpu import gpu_base
from provider.gpu import check_points
from provider.sriov import sriov_base


def run(test, params, env):
    """
    Test two VMs with GPU passthrough running simultaneously
    """

    main_vm_name = params.get("main_vm")
    main_vm = env.get_vm(main_vm_name)
    vm_names = params.get("vm_names", "").split()
    scenario = params.get("scenario", "gpu_gpu")

    vms = [main_vm]
    for vm_name in vm_names:
        vms.append(main_vm.clone(vm_name))
    if len(vms) != 2:
        test.cancel("This test requires exactly 2 VMs")

    test.log.info("TEST_SETUP: Check GPU availability")
    host_gpus = gpu_base.get_gpus_info()
    available_gpu_count = len(host_gpus)

    if scenario == "gpu_gpu":
        if available_gpu_count < 2:
            test.cancel(f"Test requires 2 GPUs but only {available_gpu_count} available")
        gpu_pcis = list(host_gpus.keys())[:2]
        test.log.info(f"Found {available_gpu_count} GPU(s), test requires 2")
        test.log.info(f"Assigning GPUs: {vms[0].name}={gpu_pcis[0]}, {vms[1].name}={gpu_pcis[1]}")
    elif scenario == "gpu_nic":
        if available_gpu_count < 1:
            test.cancel(f"Test requires 1 GPU but only {available_gpu_count} available")
        gpu_pcis = list(host_gpus.keys())[:1]
        test.log.info(f"Found {available_gpu_count} GPU(s), test requires 1")

    vmxml_backups = []
    gpu_tests = []
    managed_disabled_list = []
    dev_names = []
    sriov_test = None
    sessions = []

    for vm in vms:
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        vmxml_backups.append(vmxml.copy())

    try:
        if scenario == "gpu_gpu":
            gpu_vm_indices = [0, 1]  # Both VMs get GPUs
        elif scenario == "gpu_nic":
            gpu_vm_indices = [0]  # Only VM1 gets GPU

        test.log.info("TEST_STEP: Bind GPUs on host")
        for idx in gpu_vm_indices:
            gpu_pci = gpu_pcis[idx]
            gpu_id = host_gpus[gpu_pci].get('ID')
            current_driver = host_gpus[gpu_pci].get('driver')
            if current_driver != 'nvgrace_gpu_vfio_pci':
                test.log.info(f"Binding {gpu_pci} to nvgrace-gpu-vfio-pci")
                utils_misc.cmd_status_output("modprobe -r nouveau", ignore_status=True)
                utils_misc.cmd_status_output("modprobe nvgrace-gpu-vfio-pci")
                utils_misc.cmd_status_output(f"sh -c \"echo '{gpu_id}' > /sys/bus/pci/drivers/nvgrace_gpu_vfio_pci/new_id\"")

        for idx in gpu_vm_indices:
            test.log.info(f"TEST_STEP: Configure GPU passthrough for {vms[idx].name}")

            vm_params = params.copy()
            vm_params['gpu_pci_addr'] = gpu_base.pci_to_addr(gpu_pcis[idx])
            vm_params['main_vm'] = vms[idx].name

            gpu_test = gpu_base.GPUTest(vms[idx], test, vm_params)
            gpu_tests.append(gpu_test)

            dev_name = gpu_test.gpu_dev_name
            dev_names.append(dev_name)

            gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
            managed_disabled = gpu_hostdev_dict.get('managed') != "yes"
            managed_disabled_list.append(managed_disabled)

            gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")

        if scenario == "gpu_nic":
            test.log.info(f"TEST_STEP: Configure SR-IOV NIC passthrough for {vms[1].name}")
            # Verify PF not sole uplink (will cancel if unsafe)
            gpu_base.verify_pf_not_sole_uplink(test)

            sriov_test = sriov_base.SRIOVTest(vms[1], test, params)
            sriov_test.setup()
            test.log.info(f"Assigned NIC: {vms[1].name}=SR-IOV PF {sriov_test.pf_name} ({sriov_test.pf_pci_id})")

        test.log.info("TEST_STEP: Start both VMs")
        for vm in vms:
            vm.start()
            session = vm.wait_for_login(timeout=240)
            sessions.append(session)
            test.log.debug(f'VMXML of {vm.name}:\n{virsh.dumpxml(vm.name).stdout_text}')

        test.log.info("TEST_STEP: Verify GPU functionality")
        for idx in gpu_vm_indices:
            test.log.info(f"Verifying GPU in {vms[idx].name}")
            check_points.check_lspci(
                test,
                sessions[idx],
                eval(params.get("test_devices", '["3D"]')),
                expect_nic_exist=False
            )

            check_points.check_nvidia_smi(test, sessions[idx])

            test.log.info(f"Verifying NUMA setup in {vms[idx].name}")
            s, numa_out = sessions[idx].cmd_status_output("numactl --hardware | grep 'available.*nodes'")
            if s != 0 or "nodes (0-8)" not in numa_out:
                test.fail(f"Expected 9 NUMA nodes (0-8) in {vms[idx].name}, got: {numa_out}")

        if scenario == "gpu_nic":
            test.log.info(f"TEST_STEP: Verify SR-IOV NIC functionality in {vms[1].name}")
            s, lspci_out = sessions[1].cmd_status_output("lspci | grep -i 'ethernet\\|network'")
            if s != 0:
                test.fail(f"NIC device not found in lspci in {vms[1].name}")
            test.log.info(f"NIC device detected in {vms[1].name}: {lspci_out.strip()}")

            s, _ = sessions[1].cmd_status_output("ip link show | grep 'state UP'")
            if s != 0:
                test.fail(f"No network interface with link UP found in {vms[1].name}")

        test.log.info("TEST_STEP: Verify both VMs remain running")
        for vm in vms:
            if vm.state() != "running":
                test.fail(f"VM {vm.name} should be running, not {vm.state()}")

        for session in sessions:
            if session:
                session.close()

        test.log.info("TEST_STEP: Destroy VMs")
        for vm in vms:
            vm.destroy(gracefully=False)

        for vm in vms:
            check_points.check_qemu_log(test, vm)

    finally:
        test.log.info("TEST_TEARDOWN: Cleanup")

        for session in sessions:
            if session:
                session.close()

        if sriov_test:
            test.log.info("Tearing down SR-IOV NIC")
            sriov_test.teardown()

        for i, gpu_test in enumerate(gpu_tests):
            if gpu_test and i < len(dev_names):
                gpu_test.teardown_default(
                    managed_disabled=managed_disabled_list[i],
                    dev_name=dev_names[i]
                )

        for i, vm in enumerate(vms):
            if vm.is_alive():
                vm.destroy(gracefully=False)
            test.log.info(f"Restoring {vm.name}...")
            vmxml_backups[i].sync()
