import os
import re

from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.gpu import gpu_base


def run(test, params, env):
    """
    MIG sanity test in vm with GPU device
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    cuda_test = params.get("cuda_test")

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

        test.log.info("TEST_STEP: Copy the sample")
        guest_gpu_pci = gpu_base.get_gpu_pci(vm_session)
        cuda_samples_path = os.path.join(os.path.dirname(__file__), params.get("cuda_samples_path"))
        cuda_sample_guest_path = os.path.join("/tmp", os.path.basename(cuda_samples_path))
        vm.copy_files_to(
            cuda_samples_path, os.path.dirname(cuda_sample_guest_path),
            timeout=240)
        vm_session.cmd(f"tar -xf {cuda_sample_guest_path} -C /root")
        vm_session.cmd(f"rm -rf {cuda_sample_guest_path}")

        test.log.info("TEST_STEP: Install the driver")
        gpu_test.install_latest_driver(vm_session, True)
        gpu_test.install_cuda_toolkit(vm_session, True)

        test.log.info("TEST_STEP: Enable MIG mode")
        vm_session.cmd(f"nvidia-smi -i {guest_gpu_pci} -mig 1")
        output = vm_session.cmd_output(f"nvidia-smi -i {guest_gpu_pci} --query-gpu=pci.bus_id,mig.mode.current --format=csv,noheader")
        if "Enabled" not in output:
            test.fail("Failed to enable MIG mode!")

        test.log.info("TEST_STEP: Create GPU instances ")
        vm_session.cmd(f"nvidia-smi mig -cgi 4g.20gb,2g.10gb,1g.5gb -C")
        res = vm_session.cmd_output_safe("nvidia-smi -L")
        compute_instances = re.findall(r"Device .(\d).*UUID: (.*)\)", res)
        if not compute_instances:
            test.fail("Failed to get compute instances!")
        for dev in compute_instances:
            s, o = vm_session.cmd_status_output(f"CUDA_VISIBLE_DEVICES={dev[1]} {cuda_test}")
            test.log.debug(f"dev: {dev[1]}, output of BlackScholes: {o}")
            if s:
                test.fail("Failed to run BlackScholes test!")

        test.log.info("TEST_STEP: Destroy the GPU instances")
        res = vm_session.cmd_output_safe("nvidia-smi mig -lci | awk '/MIG/ {print $2, $3}'")
        for line in res.splitlines():
            if line:
                ids = line.split()
                test.log.debug(f"Destroy instance - {ids}")
                vm_session.cmd(f"nvidia-smi mig -dci -ci {ids[0]} -gi {ids[1]}")

        test.log.info("TEST_STEP: Disable MIG mode")
        vm_session.cmd(f"nvidia-smi -i {guest_gpu_pci} -mig 0")
        output = vm_session.cmd_output(f"nvidia-smi -i {guest_gpu_pci} --query-gpu=pci.bus_id,mig.mode.current --format=csv,noheader")
        if "Disabled" not in output:
            test.fail("Failed to disable MIG mode!")
    finally:
        gpu_test.teardown_default()
