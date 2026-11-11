import os

from virttest import data_dir
from virttest import virsh
from virttest.utils_test import libvirt

from provider.gpu import gpu_base


def run(test, params, env):
    """
    Test VM lifecycle operations with GPU passthrough
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    virsh_args = {'ignore_status': False, 'debug': True}
    unsupported_error = params.get("unsupported_error")
    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"

    try:
        gpu_test.setup_nvgrace_host_driver()
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")

        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        gpu_test.check_gpu_dev(vm)
        vm_session = vm.wait_for_login()
        gpu_test.install_latest_driver(vm_session)

        test.log.info("TEST_STEP: Reboot VM to load new NVIDIA kernel modules")
        vm.reboot(timeout=1200)
        vm_session = vm.wait_for_login(timeout=1200)

        gpu_test.nvidia_smi_check(vm_session)
        if vm_session:
            vm_session.close()

        test.log.info("TEST_STEP: Shutdown and start VM")
        virsh.shutdown(vm.name, **virsh_args, wait_for_event=True)
        if not vm.wait_for_shutdown():
            test.error('VM failed to shutdown')
        virsh.start(vm.name, **virsh_args)
        gpu_test.check_gpu_dev(vm)
        vm_session = vm.wait_for_login()
        gpu_test.nvidia_smi_check(vm_session)
        if vm_session:
            vm_session.close()

        test.log.info("TEST_STEP: Reboot VM")
        virsh.reboot(vm.name, **virsh_args)
        gpu_test.check_gpu_dev(vm)
        vm_session = vm.wait_for_login()
        gpu_test.nvidia_smi_check(vm_session)
        if vm_session:
            vm_session.close()

        test.log.info("TEST_STEP: Reset VM")
        virsh.reset(vm.name, **virsh_args)
        gpu_test.check_gpu_dev(vm)
        vm_session = vm.wait_for_login()
        gpu_test.nvidia_smi_check(vm_session)
        if vm_session:
            vm_session.close()

        test.log.info("TEST_STEP: Save the VM")
        save_path = os.path.join(data_dir.get_tmp_dir(), vm.name + '.save')
        res = virsh.save(vm.name, save_path, debug=True)
        libvirt.check_result(res, unsupported_error)
        res = virsh.managedsave(vm.name, debug=True)
        libvirt.check_result(res, unsupported_error)
        res = virsh.snapshot_create(vm.name, debug=True)
        libvirt.check_result(res, unsupported_error)
        gpu_test.check_gpu_dev(vm)

        test.log.info("TEST_STEP: Suspend and resume the VM")
        virsh.suspend(vm.name, **virsh_args)
        if not libvirt.check_vm_state(vm_name, "paused"):
            test.fail("VM should be paused")
        virsh.resume(vm.name, **virsh_args)
        if not libvirt.check_vm_state(vm_name, "running"):
            test.fail("VM should be running")
        gpu_test.check_gpu_dev(vm)

    finally:
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
