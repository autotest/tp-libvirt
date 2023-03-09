from provider.sriov import sriov_base

from virttest import virsh
from virttest.utils_libvirt import libvirt_vmxml


def run(test, params, env):
    """Hotplug/Hotunplug VF for 10 times."""
    loop_time = int(params.get("loop_time", "10"))
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)
    iface_dict = sriov_test_obj.parse_iface_dict()

    try:
        sriov_test_obj.setup_default()
        test.log.info("TEST_STEP: Start the VM")
        vm.start()
        vm.wait_for_serial_login().close()
        test.log.info("TEST_STEP: Hot plug/unplug the hostdev interface.")
        for x in range(loop_time):
            iface_dev = libvirt_vmxml.create_vm_device_by_type("interface", iface_dict)
            virsh.attach_device(vm.name, iface_dev.xml, debug=True,
                                ignore_status=False)

            virsh.detach_device(vm.name, iface_dev.xml, debug=True,
                                ignore_status=False, wait_for_event=True)
    finally:
        sriov_test_obj.teardown_default()
