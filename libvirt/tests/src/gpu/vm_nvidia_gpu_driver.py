from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.gpu import gpu_base


def run(test, params, env):
    """
    GPU driver installation/uninstallation/re-installation on vm with GPU device
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
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

        test.log.info("TEST_STEP: Install the driver")
        gpu_test.install_latest_driver(vm_session)
        gpu_test.nvidia_smi_check(vm_session)

        test.log.info("TEST_STEP: Uninstall the driver")
        vm_session.cmd("dnf module -y remove --all nvidia-driver", timeout=600)

        test.log.info("TEST_STEP: Re-install the driver")
        gpu_test.install_latest_driver(vm_session)
        gpu_test.nvidia_smi_check(vm_session)
    finally:
        gpu_test.teardown_default()
