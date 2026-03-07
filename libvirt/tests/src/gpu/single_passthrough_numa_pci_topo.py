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

    #managed_disabled = hostdev_dict.get('managed') != "yes"

    try:
        test.log.info("TEST_STEP: Configure the VM XML")
        gpu_test.setup_default(dev_name=dev_name, test_hopper_gpu="yes")
        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login(timeout=240)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        check_points.check_lspci(test, vm_session)
        check_points.check_nvidia_smi(test, vm_session)
        libvirt_vfio.check_vfio_pci(dev_pci, exp_driver="nvgrace_gpu_vfio_pci")

        test.log.info("TEST_STEP: Destroy VM")
        vm.destroy(gracefully=False)

        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                dev_pci, not managed_disabled, True), 10, 5):
            test.fail("Got incorrect driver!")
        if managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(dev_pci, status_error=True, exp_driver="nvgrace_gpu_vfio_pci")
    finally:
        gpu_test.teardown_default(
            managed_disabled=managed_disabled,
            dev_name=dev_name)
