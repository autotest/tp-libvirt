import time

from virttest import libvirt_version
from virttest import utils_test
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.gpu import gpu_base


def run(test, params, env):
    """
    Various highmem mmio size tests
    """
    def attach_gpu():
        """
        Attach a gpu device to vm

        :return: session to the vm
        """
        if hotplug:
            vm.start()
            vm_session = vm.wait_for_login()
            test.log.info("TEST_STEP: Hotplug a gpu device to vm")
            gpu_dev = libvirt_vmxml.create_vm_device_by_type("hostdev", hostdev_dict)
            virsh.attach_device(vm.name, gpu_dev.xml, **virsh_args)
            time.sleep(20)
        else:
            libvirt_vmxml.modify_vm_device(
                    vm_xml.VMXML.new_from_dumpxml(vm.name), "hostdev",
                    hostdev_dict)
            test.log.info("TEST_STEP: Start the VM with a gpu device")
            vm.start()
            vm_session = vm.wait_for_login()

        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')
        return vm_session

    def check_memory(vm_session, hostdev_pci, status_error=False):
        """
        Check the memory of a GPU device

        :param hostdev_pci: the pci address of the GPU device
        :param status_error: True if expect not existing, otherwise False
        :raises: TestFail if the result is not expected
        """
        s, o = vm_session.cmd_status_output(f"lspci -vs {hostdev_pci} |grep prefetchable")
        test.log.debug(f"The memory at device: {o}")
        msg = "Memory info should %sbe in lspci's output" % ("not " if status_error else "")
        if s ^ status_error:
            test.fail(msg)

    def prepare_controller_with_pcihole64(value, controller_idx):
        """
        Prepare the controller with a PCI hole64

        :param value: the PCI hole64 size
        :param controller_idx: the index of the controller to modify
        """
        controller_dict = {"pcihole64": value}
        libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_dumpxml(vm.name), "controller",
            controller_dict, controller_idx)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

    libvirt_version.is_libvirt_feature_supported(params)
    hotplug = params.get("hotplug", "no") == "yes"
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    virsh_args = {'ignore_status': False, 'debug': True}

    gpu_test = gpu_base.GPUTest(vm, test, params)
    hostdev_dict = gpu_test.parse_hostdev_dict()

    try:
        gpu_test.setup_default()
        vm_session = attach_gpu()

        s, o = vm_session.cmd_status_output("lspci |awk '/3D/ {print $1}'")
        hostdev_pci = o.strip()
        test.log.debug(f"The pci of hostdev device: {hostdev_pci}")
        if s:
            test.fail("Unable to get the gpu device in vm, output: %s" % o)

        check_memory(vm_session, hostdev_pci)

        if hotplug:
            test.log.info("TEST_STEP: Unplug a gpu device from vm")
            vm_hostdev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
                .devices.by_device_tag("hostdev")[0]
            virsh.detach_device(vm.name, vm_hostdev.xml,
                                wait_for_event=True, event_timeout=30,
                                **virsh_args)
        utils_test.update_boot_option(
                vm,
                args_added=f"pci=resource_alignment=38@0000:{hostdev_pci}",
            )

        vm_session.close()
        vm.destroy()
        vm_controllers = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("controller")
        for i in range(len(vm_controllers)):
            if vm_controllers[i].model == "pcie-root":
                controller_idx = i
                break

        test.log.info("TEST_STEP: Set pcihole64 to 512G")
        prepare_controller_with_pcihole64("536870912", controller_idx)

        vm_session = attach_gpu()
        check_memory(vm_session, hostdev_pci, status_error=True)

        vm_session.close()
        vm.destroy()

        test.log.info("TEST_STEP: Set pcihole64 to 1T")
        prepare_controller_with_pcihole64("1073741824", controller_idx)
        vm_session = attach_gpu()
        check_memory(vm_session, hostdev_pci, status_error=False)
    finally:
        gpu_test.teardown_default()
