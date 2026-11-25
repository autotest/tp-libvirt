import re

from virttest import utils_misc
from virttest import virsh
from virttest.utils_libvirt import libvirt_vmxml

from provider.gpu import gpu_base
from provider.gpu import check_points


def run(test, params, env):
    """
    Verify a memory device can be hotplugged to a guest with GPU
    passthrough and vCMDQ enabled
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"
    mem_dict = eval(params.get("mem_dict", "{}"))
    vm_session = None
    cmdqv_on_num = int(params.get("cmdqv_on_num", "1"))

    try:
        gpu_test.setup_nvgrace_host_driver()

        test.log.info("TEST_STEP: Configure the VM XML")
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=240)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        check_points.check_lspci(test, vm_session, eval(params.get("test_devices", '["3D"]')), expect_nic_exist=False)
        check_points.check_nvidia_smi(test, vm_session)
        check_points.check_guest_cmdqv_dmesg(test, vm_session, expect_num=cmdqv_on_num)

        def _mem_total_bytes():
            """Get total memory in bytes from guest"""

            output = vm_session.cmd_output("free -b")
            match = re.search(r"Mem:\s+(\d+)", output)
            if not match:
                test.fail(f"Could not parse memory from 'free -b' output: {output}")
            return int(match.group(1))

        test.log.info("TEST_STEP: Check initial memory size")
        initial_free = vm_session.cmd_output("free -h")
        test.log.debug(f"Initial memory:\n{initial_free}")
        initial_total = _mem_total_bytes()

        test.log.info("TEST_STEP: Hotplug memory device")
        mem_dev = libvirt_vmxml.create_vm_device_by_type("memory", mem_dict)
        virsh.attach_device(vm_name, mem_dev.xml, ignore_status=False, debug=True)

        test.log.info("TEST_STEP: Verify memory increased in guest")
        expected_increase = mem_dict['target']['size'] * 1024 ** 3  # Convert GB to bytes
        if not utils_misc.wait_for(
            lambda: _mem_total_bytes() - initial_total >= expected_increase * 0.9,
            timeout=30, step=2
        ):
            test.fail(
                f"Guest memory did not increase as expected after hotplug. "
                f"Initial: {initial_total} bytes, after: {_mem_total_bytes()} bytes, "
                f"expected increase >= {expected_increase} bytes"
            )
        new_free = vm_session.cmd_output("free -h")
        test.log.debug(f"Memory after hotplug:\n{new_free}")

        test.log.info("TEST_STEP: Verify memory device is visible via lsmem")
        lsmem_output = vm_session.cmd_output("lsmem")
        test.log.debug(f"lsmem output:\n{lsmem_output}")

        test.log.info("TEST_STEP: Verify GPU still works after memory hotplug")
        check_points.check_nvidia_smi(test, vm_session)

        if vm_session:
            vm_session.close()
        test.log.info("TEST_STEP: Destroy VM")
        vm.destroy(gracefully=False)
        check_points.check_qemu_log(test, vm)

    finally:
        if vm_session:
            vm_session.close()
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
