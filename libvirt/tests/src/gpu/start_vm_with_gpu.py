from virttest import utils_misc
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_vfio

from provider.gpu import gpu_base


def run(test, params, env):
    """
    Start vm with GPU device
    """

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    gpu_test = gpu_base.GPUTest(vm, test, params)
    dev_name = gpu_test.gpu_dev_name
    dev_pci = gpu_test.gpu_pci
    hostdev_dict = gpu_test.parse_hostdev_dict()
    managed_disabled = hostdev_dict.get('managed') != "yes"

    try:
        gpu_test.setup_default(dev_name=dev_name,
                               managed_disabled=managed_disabled)
        libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name), "hostdev",
                hostdev_dict)
        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        gpu_test.check_gpu_dev(vm)
        libvirt_vfio.check_vfio_pci(dev_pci)

        test.log.info("TEST_STEP: Destroy VM")
        vm.destroy(gracefully=False)

        if not utils_misc.wait_for(
            lambda: libvirt_vfio.check_vfio_pci(
                dev_pci, not managed_disabled, True), 10, 5):
            test.fail("Got incorrect driver!")
        if managed_disabled:
            virsh.nodedev_reattach(dev_name, debug=True, ignore_status=False)
            libvirt_vfio.check_vfio_pci(dev_pci, True)
    finally:
        gpu_test.teardown_default(
            managed_disabled=managed_disabled,
            dev_name=dev_name)
