import os

from virttest import data_dir
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.gpu import gpu_base


def run(test, params, env):
    """
    Lifecycle test on vm with GPU device
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    virsh_args = {'ignore_status': False, 'debug': True}
    unsupported_error = params.get("unsupported_error")
    gpu_test = gpu_base.GPUTest(vm, test, params)
    hostdev_dict = gpu_test.parse_hostdev_dict()

    try:
        gpu_test.setup_default()
        libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name), "hostdev",
                hostdev_dict)
        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        gpu_test.check_gpu_dev(vm)
        vm_session = vm.wait_for_login()
        gpu_test.install_latest_driver(vm_session)
        gpu_test.nvidia_smi_check(vm_session)
        vm_session.close()

        test.log.info("TEST_STEP: Shudown and start VM")
        virsh.shutdown(vm.name, **virsh_args, wait_for_event=True)
        if not vm.wait_for_shutdown():
            test.error('VM failed to shutdown in 60s')
        virsh.start(vm.name, **virsh_args)
        gpu_test.check_gpu_dev(vm)

        test.log.info("TEST_STEP: Reboot VM")
        virsh.reboot(vm.name, **virsh_args)
        gpu_test.check_gpu_dev(vm)

        test.log.info("TEST_STEP: Reset VM")
        virsh.reset(vm.name, **virsh_args)
        gpu_test.check_gpu_dev(vm)
        vm_session = vm.wait_for_login()
        gpu_test.nvidia_smi_check(vm_session)
        vm_session.close()

        test.log.info("TEST_STEP: Save the VM.")
        save_path = os.path.join(data_dir.get_tmp_dir(), vm.name + '.save')
        res = virsh.save(vm.name, save_path, debug=True)
        libvirt.check_result(res, unsupported_error)
        res = virsh.managedsave(vm.name, debug=True)
        libvirt.check_result(res, unsupported_error)
        res = virsh.snapshot_create(vm.name, debug=True)
        libvirt.check_result(res, unsupported_error)
        gpu_test.check_gpu_dev(vm)

        test.log.info("TEST_STEP: Suspend and resume the vm.")
        virsh.suspend(vm.name, **virsh_args)
        if not libvirt.check_vm_state(vm_name, "paused"):
            test.fail("VM should be paused!")
        virsh.resume(vm.name, **virsh_args)
        if not libvirt.check_vm_state(vm_name, "running"):
            test.fail("VM should be running!")
        gpu_test.check_gpu_dev(vm)

    finally:
        gpu_test.teardown_default()
