import copy
import os

from virttest import data_dir
from virttest import remote
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import disk
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

from provider.guest_os_booting import guest_os_booting_base as guest_os


def parse_disks_attrs(vmxml, test, params):
    """
    Parse disk devices' attrs

    :param vmxml: The vmxml object
    :param params: Dictionary with the test parameters
    :param test: test instance for error reporting
    :return: (Newly created disk image path, list of disk devices' attrs)
    """
    disk1_img = params.get("disk1_img")
    disk_attrs_list = []
    disk1_img_path = ""
    disk_org_attrs = vmxml.devices.by_device_tag("disk")[0].fetch_attrs()
    del disk_org_attrs["address"]
    disk1_img_url = params.get("disk1_img_url", "")
    download_disk1_img = "yes" == params.get("download_disk1_img", "no")
    firmware_type = params.get("firmware_type")
    if disk1_img:
        disk1_img_path = os.path.join(data_dir.get_data_dir(), "images", disk1_img)
        if download_disk1_img:
            if firmware_type == "ovmf":
                disk1_img_url = disk1_img_url.removesuffix('.qcow2') + '-ovmf.qcow2'
            if not disk1_img_url or not utils_misc.wait_for(
                lambda: guest_os.test_file_download(disk1_img_url, disk1_img_path), 60
            ):
                test.fail("Unable to download boot image")
        else:
            libvirt_disk.create_disk("file", disk1_img_path, disk_format="qcow2")
        disk1_attrs = copy.deepcopy(disk_org_attrs)
        disk1_attrs["source"] = {"attrs": {"file": disk1_img_path}}
        disk_attrs_list.append(disk1_attrs)

        disk_org_attrs.update(eval(params.get("disk2_attrs", "{}")))
    else:
        disk_org_attrs.update(eval(params.get("disk1_attrs", "{}")))
    disk_attrs_list.append(disk_org_attrs)
    return disk1_img_path, disk_attrs_list


def update_vm_xml(vm, params, disk_attrs_list):
    """
    Update VM xml

    :param vm: VM object
    :param params: Dictionary with the test parameters
    :param disk_attrs_list: List of disk devices' attrs
    """
    os_attrs_boots = eval(params.get("os_attrs_boots", "[]"))
    libvirt_vmxml.remove_vm_devices_by_type(vm, "disk")
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    if os_attrs_boots:
        os_attrs = {"boots": os_attrs_boots}
        vmxml.setup_attrs(os=os_attrs)
    else:
        vm_os = vmxml.os
        vm_os.del_boots()
        vmxml.os = vm_os

    index = 0
    for disk_attrs in disk_attrs_list:
        disk_obj = disk.Disk(disk_attrs)
        disk_obj.setup_attrs(**disk_attrs)
        vmxml.add_device(disk_obj)
        index += 1
        if "disk_boot_order" in params.get("shortname"):
            vmxml.set_boot_order_by_target_dev(disk_attrs["target"]["dev"], index)
    vmxml.xmltreefile.write()
    vmxml.sync()


def run(test, params, env):
    """
    Boot VM from disk devices
    This case covers per-device(disk) boot elements and os/boot elements.
    """
    vm_name = guest_os.get_vm(params)
    status_error = "yes" == params.get("status_error", "no")
    disk1_img_path = ""

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    bkxml = vmxml.copy()

    try:
        disk1_img_path, disk_attrs_list = parse_disks_attrs(vmxml, test, params)
        update_vm_xml(vm, params, disk_attrs_list)
        test.log.debug(vm_xml.VMXML.new_from_dumpxml(vm.name))

        vm.start()
        try:
            vm.wait_for_serial_login().close()
        except remote.LoginTimeoutError as detail:
            if status_error:
                test.log.debug("Found the expected error: %s", detail)
            else:
                test.fail(detail)

    finally:
        bkxml.sync()
        if disk1_img_path and os.path.isfile(disk1_img_path):
            test.log.debug(f"removing {disk1_img_path}")
            os.remove(disk1_img_path)
