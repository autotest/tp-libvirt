import os
import time
import shutil
import logging
import ast

from aexpect.exceptions import ExpectProcessTerminatedError

from avocado.utils.download import url_download

from virttest.data_dir import get_data_dir
from virttest.libvirt_xml import vm_xml
from virttest.remote import LoginTimeoutError
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt.libvirt_bios import remove_bootconfig_items_from_vmos
from virttest.utils_test import libvirt

LOG = logging.getLogger("avocado")


def counter():
    """
    When used as generator this function returns increasing integers.
    :returns i: Integer each time next(some_counter) is called it is bigger by 1
    """
    i = 0
    while True:
        val = (yield i)
        # If value provided, change counter
        if val is not None:
            i = val
        else:
            i += 1


def clean_up_vmxml(vm, params, index_counter):
    """
    Removes some elements from VM XML so they can be set up later in the test.

    :param vm: Avocado VM object
    :param params: Test parameters object
    :param index_counter: Instance of the counter function to use for iteration
    """

    remove_boot_devices = "yes" == params.get("remove_boot_devices")
    xml_boot_in_os = "yes" == params.get("xml_boot_in_os", "yes")
    boot_dev = params.get("boot_dev", "hd")
    if boot_dev == "hd" or remove_boot_devices:
        libvirt_vmxml.remove_vm_devices_by_type(vm, device_type='disk')
    else:
        # We increase device index counter so that we do not modify first dev
        next(index_counter)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)

    if not xml_boot_in_os:
        vmxml.remove_all_boots()
        LOG.info("Removed all boot elements from VMXML")
    return vmxml


def set_domain_disks(vm, vmxml, source_file, params, unbootable_source=None):
    """
    Replace the domain disk with new setup device or download image

    :param vm: vm object
    :param vmxml: The instance of VMXML class
    :param source_file: The domain disk image path
    :param params: Avocado params object
    :param unbootable_source: Path to source that should be un-bootable, similar to source_file
    """

    index_counter = counter()
    boot_dev = params.get("boot_dev", "hd")
    disk_device = params.get("disk_device", "disk")
    target_dev = params.get("target_dev", "sdb")
    unbootable_target_dev = params.get("unbootable_target_dev", "sda")
    target_bus = params.get("target_bus", "sata")
    driver_name = params.get("driver_name", "qemu")
    driver_type = params.get("driver_type", "qcow2")
    use_bootable_dev = params.get("use_bootable_dev", "no") == "yes"
    use_unbootable_dev = "yes" == params.get("use_unbootable_dev", "no")
    boot_order_bootable_first = "yes" == params.get("boot_order_bootable_first")
    xml_boot_in_os = "yes" == params.get("xml_boot_in_os", "yes")
    second_target_dev = params.get("second_target_dev")
    cd_image_filename = params.get("cd_image_filename", "boot.iso")
    second_cd_image_filename = params.get("second_cd_image_filename", "boot2.iso")
    boot = None

    vmxml = clean_up_vmxml(vm, params, index_counter)

    if use_unbootable_dev:
        if boot_dev == "cdrom":
            unbootable_source = None

        if not xml_boot_in_os and not boot_order_bootable_first:
            boot = 1

        unbootable_disk_params = set_up_disk_params(
            driver_name, driver_type, unbootable_target_dev, target_bus,
            disk_device, source=unbootable_source, boot=boot)
        libvirt_vmxml.modify_vm_device(vmxml=vmxml, dev_type='disk',
                                       dev_dict=unbootable_disk_params,
                                       index=next(index_counter))

    if use_bootable_dev:
        if boot_dev == "cdrom":
            bootable_source = get_data_dir() + "/" + cd_image_filename
        else:
            bootable_source = source_file
        if not xml_boot_in_os:
            if boot_order_bootable_first:
                boot = 1
            else:
                boot = 2

        bootable_disk_params = set_up_disk_params(
            driver_name, driver_type, target_dev, target_bus, disk_device,
            source=bootable_source, boot=boot)
        libvirt_vmxml.modify_vm_device(vmxml=vmxml, dev_type='disk',
                                       dev_dict=bootable_disk_params,
                                       index=next(index_counter))

    if second_target_dev:
        bootable_source = get_data_dir() + "/" + second_cd_image_filename
        bootable_disk_params = set_up_disk_params(
            driver_name, driver_type, second_target_dev, target_bus, disk_device,
            source=bootable_source)
        libvirt_vmxml.modify_vm_device(vmxml=vmxml, dev_type='disk',
                                       dev_dict=bootable_disk_params,
                                       index=next(index_counter))


def set_up_os_xml(vmxml, dict_os_attrs=None):
    """
    Set up elements in <os> element in VMXML, depending on test case that is
    executed.

    :param vmxml: VMXML instance to modify
    :param dict_os_attrs: Dict containing os attributes to set up in OS element
    """
    remove_bootconfig_items_from_vmos(vmxml["os"])
    if dict_os_attrs:
        vmxml.set_os_attrs(**ast.literal_eval(dict_os_attrs))
    vmxml.sync()


def set_up_disk_params(driver_name, driver_type, target_dev, target_bus,
                       disk_device, source=None, boot=None):
    """
    Prepares dict with parameters for creation of device of type disk.

    :param driver_name: String, name of the disk driver ex.: qemu
    :param driver_type: String, type of the disk driver ex.: qcow2
    :param target_dev: String, target device identifier ex.: sdb
    :param target_bus: String, type of target bus ex.: scsi
    :param disk_device: String, type of disk device ex.: cdrom
    :param source: String, path to device image source
    :param boot: Int, boot order element
    """
    disk_params = {'driver': {'name': driver_name, 'type': driver_type},
                   'target': {'dev': target_dev, 'bus': target_bus},
                   'device': disk_device}
    if source:
        disk_params.update({'source': {'attrs': {'file': source}}})
    if boot:
        disk_params.update({"boot": boot})
    return disk_params


def cleanup_test(test, vm, vmxml_backup, unbootable_source, cd_image_filename, second_cd_image_filename):
    """
    Perform steps to remove modifications that happened during the test.

    :param test: avocado test object
    :param vm: avocado vm object
    :param vmxml_backup: VMXMl object to restore
    :param unbootable_source:  un-bootable source variable if it was used otherwise None
    :param cd_image_filename: cd_image_filename variable to identify file to be cleaned up
    :param second_cd_image_filename: second_cd_image_filename variable to identify file to
    be cleaned up
    """
    test.log.info("Start to cleanup")
    if vm.is_alive:
        test.log.info("Destroying VM")
        vm.destroy()
    if unbootable_source and os.path.exists(unbootable_source):
        test.log.info("Removing unbootable source disk.")
        os.remove(unbootable_source)
    if cd_image_filename and os.path.exists(cd_image_filename):
        test.log.info(f"Removing {cd_image_filename} file")
        os.remove(get_data_dir() + "/" + cd_image_filename)
    if second_cd_image_filename and os.path.exists(second_cd_image_filename):
        test.log.info(f"Removing {second_cd_image_filename} file")
        os.remove(get_data_dir() + "/" + second_cd_image_filename)
    test.log.info("Restoring the VM XML")
    vmxml_backup.sync()


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
    boot_dev = params.get("boot_dev", "hd")
    cd_image_url = params.get("cd_image_url")
    cd_image_filename = params.get("cd_image_filename", "boot.iso")
    second_cd_image_filename = params.get("second_cd_image_filename", "boot2.iso")
    use_unbootable_dev = "yes" == params.get("use_unbootable_dev", "no")
    dict_os_attrs = params.get("dict_os_attrs")
    cd_boot_message = params.get("cd_boot_message")
    disk_type = params.get("disk_type")
    image_size = params.get("image_size")
    disk_format = params.get("disk_format")
    second_target_dev = params.get("second_target_dev")
    vm = env.get_vm(vm_name)
    source_file = vm.get_first_disk_devices()['source']

    if use_unbootable_dev:
        unbootable_source = os.path.join(os.path.dirname(source_file), "unbootable.img")
        libvirt.create_local_disk(disk_type, unbootable_source, image_size, disk_format)
        params["unbootable_source"] = unbootable_source
    else:
        unbootable_source = None

    # Back VM XML
    vmxml_backup = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

    # Remove loader/nvram element if exist for seabios cases
    for item in ["loader", "nvram"]:
        if item in str(vmxml):
            vmxml.xmltreefile.remove_by_xpath("/os/%s" % item, remove_all=True)
            vmxml.sync()

    if boot_dev == "cdrom":
        if cd_image_url == "CD_IMAGE_URL":
            test.cancel("cd_image_url variable not set by libvirt-ci")
        else:
            url_download(cd_image_url, get_data_dir() + "/" + cd_image_filename)
        if second_target_dev:
            shutil.copy(
                get_data_dir() + "/" + cd_image_filename,
                get_data_dir() + "/" + second_cd_image_filename)
    set_up_os_xml(vmxml, dict_os_attrs)
    try:
        set_domain_disks(vm, vmxml, source_file, params, unbootable_source)
        test.log.debug(f"VM XML before start:\n{vm_xml.VMXML.new_from_dumpxml(vm_name)}")
        if not vm.is_alive():
            vm.start()
        if not status_error:
            try:
                if boot_dev == "cdrom":
                    try:
                        match, text = vm.serial_console.read_until_any_line_matches(
                            [cd_boot_message], timeout=60,
                            internal_timeout=0.5)
                    except ExpectProcessTerminatedError:
                        vm.cleanup_serial_console()
                        vm.create_serial_console()
                        match, text = vm.serial_console.read_until_any_line_matches(
                            [cd_boot_message], timeout=60,
                            internal_timeout=0.5)
                else:
                    time.sleep(3)
                    vm.wait_for_serial_login(timeout=15, recreate_serial_console=True)
            except Exception as e:
                test.fail(f"Test fail: {str(e)}")
            else:
                test.log.debug("Succeed to boot %s", vm_name)
        else:
            try:
                vm.wait_for_serial_login(timeout=15, recreate_serial_console=True)
            except LoginTimeoutError as expected_e:
                test.log.debug("Got expected error message: %s", str(expected_e))
            except Exception as exc:
                test.fail("Got unexpected error message: %s", str(exc))
            else:
                test.fail("The guest should not be successfully connected")
    finally:
        cleanup_test(test, vm, vmxml_backup, unbootable_source,
                     cd_image_filename, second_cd_image_filename)
