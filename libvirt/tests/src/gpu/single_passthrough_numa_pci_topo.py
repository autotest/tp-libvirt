from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_vfio

from provider.gpu import gpu_base
from provider.gpu import check_points


def run(test, params, env):
    """
    Start vm with GPU device
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    dev_pci = gpu_test.gpu_pci
    gpu_hostdev_dict = gpu_test.parse_hostdev_dict()
    gpu_managed_disabled = gpu_hostdev_dict.get('managed') != "yes"

    try:
        test.log.info("TEST_STEP: Configure the VM XML")
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")
        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=240)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        check_points.check_lspci(
            test,
            vm_session,
            eval(params.get("test_devices")),
            expect_nic_exist=False
        )
        check_points.check_nvidia_smi(test, vm_session)
        cmdqv_on_num = int(params.get("cmdqv_on_num", "1"))
        check_points.check_guest_cmdqv_dmesg(test, vm_session, expect_num=cmdqv_on_num)
        libvirt_vfio.check_vfio_pci(dev_pci, exp_driver="nvgrace_gpu_vfio_pci")
        test.log.info("Verify: GPU driver is nvgrace_gpu_vfio_pci - PASS")
        if vm_session:
            vm_session.close()
        test.log.info("TEST_STEP: Destroy VM")
        vm.destroy(gracefully=False)

        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                dev_pci, not gpu_managed_disabled, True), 10, 5):
            test.fail("Got incorrect driver!")
        if gpu_managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(dev_pci, True)

        check_points.check_qemu_log(test, vm)
    finally:
        gpu_test.teardown_default(
            managed_disabled=gpu_managed_disabled,
            dev_name=dev_name
        )
