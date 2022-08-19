import os

from virttest.libvirt_xml import vm_xml
from virttest.remote import LoginTimeoutError
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


# Global test env cleanup variables
unbootable_source = None


def set_domain_disk(vm, vmxml, blk_source, params):
    """
    Replace the domain disk with new setup device or download image

    :param vmxml: The instance of VMXML class
    :param blk_source: The domain disk image path
    :param params: Avocado params object
    """

    disk_device = params.get("disk_device", "disk")
    disk_type = params.get("disk_type", "file")
    target_dev = params.get("target_dev", "sdb")
    unbootable_target_dev = params.get("unbootable_target_dev", "sda")
    target_bus = params.get("target_bus", "sata")
    disk_format = params.get("disk_format", "qcow2")
    image_size = params.get("image_size", "1G")
    driver_name = params.get("driver_name", "qemu")
    driver_type = params.get("driver_type", "qcow2")
    use_bootable_dev = params.get("use_bootable_dev", "no") == "yes"
    use_unbootable_dev = "yes" == params.get("use_unbootable_dev", "no")
    use_unbootable_dev_first = "yes" == params.get("use_unbootable_dev_first", "no")
    boot_order_bootable_first = "yes" == params.get("boot_order_bootable_first")

    global unbootable_source

    # use_unbootable_dev_first means the xml contain both type of device
    # the one which is not bootable has higher order without "boot order" element
    if use_unbootable_dev_first:
        use_bootable_dev = use_unbootable_dev_first
        use_unbootable_dev = use_unbootable_dev_first

    #Remove all disk and reacquire the vmxml
    libvirt_vmxml.remove_vm_devices_by_type(vm, device_type='disk')
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    vmxml.remove_all_boots()

    if use_unbootable_dev:
        device_path = os.path.dirname(blk_source)
        unbootable_source = os.path.join(device_path, "unbootable.img")
        unbootable_disk_params = {'source': {'attrs': {'file': unbootable_source}},
                                  'driver': {'name': driver_name, 'type': driver_type},
                                  'target': {'dev': unbootable_target_dev, 'bus': target_bus},
                                  'device': disk_device}
        if not boot_order_bootable_first:
            unbootable_disk_params["boot"] = 1

        libvirt.create_local_disk(disk_type, unbootable_source, image_size, disk_format)
        libvirt_vmxml.modify_vm_device(vmxml=vmxml, dev_type='disk',
                                       dev_dict=unbootable_disk_params, index=0)

    if use_bootable_dev:
        bootable_source = blk_source
        bootable_disk_params = {'source': {'attrs': {'file': bootable_source}},
                                'driver': {'name': driver_name, 'type': driver_type},
                                'target': {'dev': target_dev, 'bus': target_bus},
                                'device': disk_device}
        if boot_order_bootable_first:
            bootable_disk_params["boot"] = 1
        else:
            bootable_disk_params["boot"] = 2

        libvirt_vmxml.modify_vm_device(vmxml=vmxml, dev_type='disk',
                                       dev_dict=bootable_disk_params, index=1)


def run(test, params, env):
    """
    Test Boot order on Seabios Guest with options

    Steps:
    1) Edit VM xml with specified options
    2) Start the guest, and try to login to the guest
    3) Verify the guest can be successfully connected in positive scenarios
       and check if the error messages is expected in negative scenarios
    """
    vm_name = params.get("main_vm", "")
    status_error = params.get("status_error", "no") == "yes"
    vm = env.get_vm(vm_name)

    # Back VM XML
    vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    # Remove loader/nvram element if exist for seabios cases
    for item in ["loader", "nvram"]:
        if item in str(vmxml):
            vmxml.xmltreefile.remove_by_xpath("/os/%s" % item, remove_all=True)
            vmxml.sync()

    try:
        blk_source = vm.get_first_disk_devices()['source']
        set_domain_disk(vm, vmxml, blk_source, params)
        if not vm.is_alive():
            vm.start()
            test.log.debug(f"VM XML after start:\n{vm_xml.VMXML.new_from_dumpxml(vm_name)}")
        if not status_error:
            try:
                vm.cleanup_serial_console()
                vm.create_serial_console()
                vm.wait_for_serial_login(timeout=15)
            except Exception as e:
                test.fail(f"Test fail: {str(e)}")
            else:
                test.log.debug("Succeed to boot %s", vm_name)
        else:
            try:
                vm.cleanup_serial_console()
                vm.create_serial_console()
                vm.wait_for_serial_login(timeout=15)
            except LoginTimeoutError as expected_e:
                test.log.debug("Got expected error message: %s", str(expected_e))
            except Exception as e:
                test.fail("Got unexpected error message: %s", str(e))
            else:
                test.fail("The guest should not be successfully connected")
    finally:
        test.log.info("Start to cleanup")
        if vm.is_alive:
            vm.destroy()
        if unbootable_source:
            os.remove(unbootable_source)
        test.log.info("Restore the VM XML")
        vmxml_backup.sync()
