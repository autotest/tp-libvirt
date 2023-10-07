from virttest import remote
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.guest_os_booting import guest_os_booting_base


def run(test, params, env):
    """
    Test whether VM without a bootable disk cannot boot
    """
    vm_name = guest_os_booting_base.get_vm(params)
    remove_devices = eval(params.get("remove_device_types", "[]"))
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    bkxml = vmxml.copy()

    try:
        test.log.info("TEST_SETUP: Remove disk device and interface device.")
        for dev in remove_devices:
            libvirt_vmxml.remove_vm_devices_by_type(vm, dev)

        test.log.info("TEST_STEP: Check if VM is working.")
        vm.start()
        try:
            vm.wait_for_serial_login().close()
        except remote.LoginTimeoutError as detail:
            test.log.debug("Found the expected error: %s", detail)
        else:
            test.fail("VM without bootable devices should be inaccessible!")
    finally:
        test.log.info("TEST_TEARDOWN: Recover test environment.")
        bkxml.sync()
