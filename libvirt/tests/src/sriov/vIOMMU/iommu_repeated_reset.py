from time import sleep
from random import uniform

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml

from provider.viommu import viommu_base

from provider.vm_log_file import vm_log_file


def run(test, params, env):
    """
    Start vm with iommu device and kinds of virtio devices with iommu=on, and
    check network and disk function.
    """

    def setup_test():
        vm_log.clear_vm_logfile()
        test_obj.setup_iommu_test(iommu_dict=iommu_dict, cleanup_ifaces=cleanup_ifaces)
        test_obj.prepare_controller()
        test.log.debug("---------------------------------------------------------------------------")
        dev_dict = eval(params.get('disk_dict', '{}'))
        test.log.debug(f"disk_dict: {dev_dict}")
        if dev_dict:
            dev_dict = test_obj.update_disk_addr(dev_dict)
            if dev_dict["target"].get("bus") != "virtio":
                libvirt_vmxml.modify_vm_device(
                   vm_xml.VMXML.new_from_dumpxml(vm.name), 'disk', {'driver': None})

            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm.name), 'disk', dev_dict)

        if cleanup_ifaces:
            libvirt_vmxml.modify_vm_device(
                    vm_xml.VMXML.new_from_dumpxml(vm.name),
                    "interface", iface_dict)

    cleanup_ifaces = "yes" == params.get("cleanup_ifaces", "yes")
    iommu_dict = eval(params.get('iommu_dict', '{}'))
    iface_dict = eval(params.get('iface_dict', '{}'))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm_log = vm_log_file.VMLogFile(vm_name, test)
    vm = env.get_vm(vm_name)

    test_obj = viommu_base.VIOMMUTest(vm, test, params)

    try:
        setup_test()

        test.log.info("TEST_STEP: Start the VM and wait for login.")
        vm.start()
        vm.wait_for_login()
        test.log.info("TEST_STEP: Reboot the VM and wait for event 'reboot'")
        res = virsh.reboot(vm_name, wait_for_event=True)
        if res.exit_status != 0:
            test.fail(f"Unexpected result when rebooting VM:{res.stderr}")
        test.log.debug(f"reboot result: {res}")

        max_wait_ms = int(params.get('max_wait_ms', 0))
        max_repeat = int(params.get('max_repeat', 10))

        for _ in range(max_repeat):
            if max_wait_ms > 0:
                wait_time = uniform(0, max_wait_ms) / 1000
                test.log.debug(f"waiting before reset {wait_time}s")
                sleep(wait_time)
            virsh.reset(vm_name)

        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))

        log_messages = params.get('log_messages', "")
        fail_if_found = params.get('fail_if_found', True)
        test.log.debug("TEST_STEP: check log for error messages")
        vm_log.check_log(log_messages, fail_if_found)

    finally:
        test_obj.teardown_iommu_test()
        vm_log.restore_log()
