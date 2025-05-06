from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.gpu import gpu_base


def run(test, params, env):
    """
    Start vm with GPU device with managed=no or ignored
    """
    error_msg = params.get("error_msg", "Unmanaged")
    test_scenario = params.get("test_scenario")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    gpu_test = gpu_base.GPUTest(vm, test, params)
    hostdev_dict = gpu_test.parse_hostdev_dict()

    try:
        gpu_test.setup_default()
        if test_scenario == "define_start":
            test.log.info("TEST_STEP: Start the VM")
            libvirt_vmxml.modify_vm_device(
                    vm_xml.VMXML.new_from_dumpxml(vm.name), "hostdev",
                    hostdev_dict)
            res = virsh.start(vm.name, debug=True)
        else:
            test.log.info("TEST_STEP: Hotplug a gpu device")
            vm.start()
            vm.wait_for_login().close()
            gpu_dev = libvirt_vmxml.create_vm_device_by_type("hostdev", hostdev_dict)
            res = virsh.attach_device(vm.name, gpu_dev.xml, debug=True)
        libvirt.check_result(res, error_msg)

    finally:
        gpu_test.teardown_default()
