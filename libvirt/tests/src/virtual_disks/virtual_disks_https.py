from virttest import virsh
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt
from virttest.utils_libvirtd import Libvirtd
from virttest.utils_config import LibvirtQemuConfig


def run(test, params, env):
    """
    Start vm and hotplug/unplug https network disk with different options.

    1. Start vm with/without https network disk.
    2. Hot-unplug https network disk.
    3. Hotplug the https network disk.
    """
    def setup_test():
        """
        Prepare a https network disk test environment.

        :param qemu_config: return the qemu config.
        """
        qemu_config = LibvirtQemuConfig()
        qemu_config.storage_use_nbdkit = 1
        Libvirtd('virtqemud').restart()
        return qemu_config

    def prepare_disk():
        """
        Prepare the https network disk.

        :param disk_obj: return the disk object.
        """
        network_device = params.get("network_device")
        disk_dict = eval(params.get("disk_dict", "{}") % network_device)
        disk_obj = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
        if not with_hotplug:
            libvirt.add_vm_device(vmxml, disk_obj)
        vmxml.sync()
        return disk_obj

    def check_result(disk_in_vm=True):
        """
        Check the test result.

        :param disk_in_vm: boolean value to make sure if disk in vm.
        """
        if disk_in_vm:
            xml_after_adding_device = vm_xml.VMXML.new_from_dumpxml(vm_name)
            libvirt_vmxml.check_guest_xml_by_xpaths(xml_after_adding_device, expected_xpaths)
            libvirt_vmxml.check_guest_xml(vm_name, cookie_in_dumpxml,
                                          option=" --security-info")
        else:
            domblklist_result = virsh.domblklist(vm_name, debug=True).stdout_text.strip()
            if target_disk in domblklist_result:
                test.fail("The target disk %s can't be detached from guest." % target_disk)

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    target_disk = params.get("target_disk")
    expected_xpaths = eval(params.get("expected_xpaths"))
    cookie_in_dumpxml = params.get("cookie_in_dumpxml")
    with_hotplug = "yes" == params.get("with_hotplug", "no")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    try:
        qemu_config = setup_test()
        disk_dev = prepare_disk()
        test.log.info("TEST_STEP1: Start the guest.")
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test.log.debug("The current guest xml is: %s", virsh.dumpxml(vm_name).stdout_text)
        disk_in_vm = not with_hotplug
        check_result(disk_in_vm)
        if with_hotplug:
            test.log.info("TEST_STEP2: Hotplug the disk.")
            virsh.attach_device(vm_name, disk_dev.xml, debug=True, ignore_status=False)
            check_result()
            test.log.info("TEST_STEP3: Hot-unplug the disk.")
            virsh.detach_device(vm_name, disk_dev.xml, debug=True, ignore_status=False)
            check_result(disk_in_vm=False)
    finally:
        backup_vmxml.sync()
        qemu_config.restore()
        Libvirtd('virtqemud').restart()
