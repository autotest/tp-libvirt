import os
from time import sleep
from random import uniform

from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt
from virttest import utils_logfile

from provider.viommu import viommu_base


def run(test, params, env):
    """
    Start vm with iommu device and kinds of virtio devices with iommu=on, and
    check network and disk function.
    """

    def setup_test():
        utils_logfile.clear_log_file(log_file_name, '/var/log/libvirt/qemu')
        test_obj.setup_iommu_test(iommu_dict=iommu_dict, cleanup_ifaces=False)
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

    iommu_dict = eval(params.get('iommu_dict', '{}'))

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    log_file_name = f"{vm_name}.log"
    log_file = os.path.join("/var/log/libvirt/qemu", log_file_name)
    vm = env.get_vm(vm_name)

    test_obj = viommu_base.VIOMMUTest(vm, test, params)

    try:
        setup_test()

        test.log.info("TEST_STEP: Start the VM and wait for login.")
        vm.start()
        vm.wait_for_login().close()
        test.log.info("TEST_STEP: Reboot the VM and wait for event 'reboot'")
        virsh.reboot(vm_name, wait_for_event=True, debug=True, ignore_status=False)
        test.log.debug("TEST_STEP: verify user is able to login also after restarts")
        vm.wait_for_login().close()

        max_wait_ms = int(params.get('max_wait_ms', 0))
        max_repeat = int(params.get('max_repeat', 10))

        for _ in range(max_repeat):
            if max_wait_ms > 0:
                wait_time = uniform(0, max_wait_ms) / 1000
                test.log.debug(f"waiting before reset {wait_time}s")
                sleep(wait_time)
            virsh.reset(vm_name)

        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))

        test.log.debug("TEST_STEP: verify user is able to login also after restarts")
        vm.wait_for_login().close()
        log_messages = params.get('log_messages', "")
        str_in_log = (params.get('str_in_log', "False") == "True")
        test.log.debug("TEST_STEP: check log for error messages")
        libvirt.check_logfile(log_messages, log_file, str_in_log)

    finally:
        test_obj.teardown_iommu_test()
