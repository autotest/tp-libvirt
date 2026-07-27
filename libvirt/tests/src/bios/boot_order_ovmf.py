import os
import time

from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.remote import LoginTimeoutError
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def check_boot(vm, test, params):
    """
    Check if the VM can boot up.

    :param vm: VM object
    :param test: Avocado test object
    :param params: Avocado params object
    """
    status_error = params.get("status_error", "no") == "yes"
    test.log.debug(f"VMXML before start\n{vm_xml.VMXML.new_from_dumpxml(vm.name)}")
    if not vm.is_alive():
        vm.start()
    time.sleep(3)
    if not status_error:
        try:
            vm.wait_for_serial_login(recreate_serial_console=True)
        except Exception as error:
            test.fail(f"Test fail: {error}")
        else:
            test.log.debug("Succeed to boot %s", vm.name)
    else:
        try:
            vm.wait_for_serial_login(recreate_serial_console=True)
        except LoginTimeoutError as expected_e:
            test.log.debug(f"Got expected error message: {expected_e}")
        except Exception as e:
            test.fail(f"Got unexpected error message: {e}")
        else:
            test.fail("The guest should not be successfully connected")


def set_domain_disk(vm, blk_source, params):
    """
    Replace the domain disk with newly setup device

    :param vm: Avocado VM object
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
    xml_boot_in_os = "yes" == params.get("xml_boot_in_os")

    # use_unbootable_dev_first means the xml contain both type of device
    # the one which is not bootable has higher order without "boot order" element
    if use_unbootable_dev_first:
        use_bootable_dev = use_unbootable_dev_first
        use_unbootable_dev = use_unbootable_dev_first

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm.name)
    vmxml.remove_all_disk()
    if not xml_boot_in_os:
        vmxml.remove_all_boots()
    vmxml.sync()
    if use_unbootable_dev:
        unbootable_source = os.path.join(data_dir.get_data_dir(), "unbootable.img")
        unbootable_disk_params = {'source': {'attrs': {'file': unbootable_source}},
                                  'driver': {'name': driver_name, 'type': driver_type},
                                  'target': {'dev': unbootable_target_dev, 'bus': target_bus},
                                  'device': disk_device}
        if not xml_boot_in_os and not boot_order_bootable_first:
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
        if not xml_boot_in_os:
            if boot_order_bootable_first:
                bootable_disk_params["boot"] = 1
            else:
                bootable_disk_params["boot"] = 2

        libvirt_vmxml.modify_vm_device(vmxml=vmxml, dev_type='disk',
                                       dev_dict=bootable_disk_params, index=1)


def setup_test(params, env):
    """
    Perform steps that prepare VM and environment for testing.

    :param params: Avocado params object
    :param env: Avocado env object
    """
    vm_name = params.get("main_vm", "")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    loader_location = params.get("loader_location")

    features_xml = vmxml.features
    features_xml.smm = "on"
    vmxml.features = features_xml

    osxml = vmxml.os
    os_attrs = osxml.fetch_attrs()
    if (os_attrs.get('os_firmware') != 'efi' or
            os_attrs.get('loader_type') != 'pflash'):
        dict_os_attrs = {"loader": loader_location, "loader_type": "pflash",
                         "loader_readonly": "yes", "secure": "yes"}
        vmxml.set_os_attrs(**dict_os_attrs)
        vmxml.sync()


def execute_test(vm, test, params):
    """
    Perform the test itself.

    :param vm: Avocado VM object
    :param test: Avocado test object
    :param params: Avocado params object
    """
    try:
        blk_source = vm.get_first_disk_devices()['source']
        set_domain_disk(vm, blk_source, params)
        check_boot(vm, test, params)
    except LoginTimeoutError:
        test.log.debug("Got expected error message: %s", str(LoginTimeoutError))


def cleanup_test(vm, vmxml_backup, test):
    """
    :param vm: VM object
    :param vmxml_backup: VMXML object copy made at the start of the test
    :param test: Avocado test object
    """
    test.log.info("Start to cleanup")
    if vm.is_alive:
        vm.destroy()
    test.log.info("Restore the VM XML")
    vmxml_backup.sync()
    test.log.info("Remove unbootable image if it exists.")
    unbootable_image_path = data_dir.get_data_dir() + "unbootable.img"
    if os.path.exists(unbootable_image_path):
        os.remove(unbootable_image_path)


def run(test, params, env):
    """
    Function executed by avocado framework that executes the test.

    :param test: Avocado test object
    :param params: Avocado params object
    :param env: Avocado environment object
    """
    vm_name = params.get("main_vm", "")
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    vm = env.get_vm(vm_name)

    setup_test(params, env)
    try:
        execute_test(vm, test, params)
    finally:
        cleanup_test(vm, vmxml_backup, test)
