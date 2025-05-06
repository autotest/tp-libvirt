from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.gpu import gpu_base


def run(test, params, env):
    """
    Hotplug/unplug GPU device
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    virsh_args = {'ignore_status': False, 'debug': True}

    gpu_test = gpu_base.GPUTest(vm, test, params)
    hostdev_dict = gpu_test.parse_hostdev_dict()

    try:
        gpu_test.setup_default()
        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm_session = vm.wait_for_login()

        test.log.info("TEST_STEP: Hotplug a gpu device to vm")
        gpu_dev = libvirt_vmxml.create_vm_device_by_type("hostdev", hostdev_dict)
        virsh.attach_device(vm.name, gpu_dev.xml, **virsh_args)
        gpu_test.check_gpu_dev(vm)
        test.log.debug(f'VMXML of {vm_name}:\n{virsh.dumpxml(vm_name).stdout_text}')

        gpu_test.install_latest_driver(vm_session)
        gpu_test.nvidia_smi_check(vm_session)

        test.log.info("TEST_STEP: Unplug a gpu device from vm")
        vm_hostdev = vm_xml.VMXML.new_from_dumpxml(vm.name)\
            .devices.by_device_tag("hostdev")[0]
        virsh.detach_device(vm.name, vm_hostdev.xml,
                            wait_for_event=True, event_timeout=30,
                            **virsh_args)
        gpu_test.check_gpu_dev(vm, True)

        test.log.info("TEST_STEP: Hotplug back the gpu device")
        virsh.attach_device(vm.name, gpu_dev.xml, **virsh_args)
        gpu_test.check_gpu_dev(vm)
        gpu_test.nvidia_smi_check(vm_session)

        test.log.info("TEST_STEP: Check gpu device exclusivity")
        res = virsh.attach_device(vm.name, gpu_dev.xml, debug=True)
        libvirt.check_result(res, "in use")
        gpu_test.check_gpu_dev(vm)
        gpu_test.nvidia_smi_check(vm_session)
        vm_session.close()
    finally:
        gpu_test.teardown_default()
