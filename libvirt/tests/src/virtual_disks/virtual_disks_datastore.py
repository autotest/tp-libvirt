import os

from avocado.utils import process

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_disk
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Start guest with datastore and hotplug disk with datastore.
    """
    def prepare_disk():
        """
        Prepare the disk.

        :params return: return the data file of new image.
        """
        if with_block:
            data_file = libvirt.setup_or_cleanup_iscsi(is_setup=True, is_login=True)
        else:
            data_file = data_dir.get_data_dir() + '/datastore.img'
            libvirt.create_local_disk("file", data_file, disk_size, "raw")
        return data_file

    def prepare_nested_xml(data_file, disk_dict):
        """
        Prepare the nested xml of disk xml.

        :param data_file: the data file used to create image.
        :param disk_dict: the disk dict.
        :return: the tuple of active image path, data file option and disk dict.
        """
        base_image_path = data_dir.get_data_dir() + "/base_image.qcow2"
        active_image_path = data_dir.get_data_dir() + "/active_image.qcow2"
        fir_data_file = data_file
        sec_data_file = data_dir.get_data_dir() + "/sec_datastore.raw"
        images_files = [
            (base_image_path, fir_data_file),
            (active_image_path, sec_data_file)
        ]
        for image_path, data_file in images_files:
            file_list.extend([image_path, data_file])
            data_file_option = params.get("data_file_option") % data_file
            libvirt.create_local_disk(
                    "file", image_path, disk_size, disk_image_format,
                    extra=data_file_option)
        rebase_cmd = "qemu-img rebase -f qcow2 -b %s -F qcow2 %s" % (base_image_path, active_image_path)
        process.run(rebase_cmd, shell=True, ignore_status=False)
        if with_backing_images:
            source_dict = eval(params.get("source_dict") % (active_image_path, sec_data_file, base_image_path,
                                                            base_image_path, fir_data_file))
            disk_dict = disk_dict | source_dict
        return active_image_path, data_file_option, disk_dict

    def hotplug_disk(data_file_option):
        """
        Hotplug disk device.

        :params data_file_option: the option to create image with data file
        :params return: return the new image path
        """
        if not vm.is_alive():
            vm.start()
        _, new_image_path = disk_obj.prepare_disk_obj(disk_type, disk_dict, new_image_path="",
                                                      extra=data_file_option)
        disk_dict.update({'source': {'attrs': {'file': new_image_path}}})
        disk_dev = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
        virsh.attach_device(vm_name, disk_dev.xml, debug=True, ignore_status=False, wait_for_event=True)
        return new_image_path, disk_dev

    def check_result():
        """
        Check the test result.
        """
        if not vm.is_alive():
            vm.start()
        vm_session = vm.wait_for_login()
        disk_xml = virsh.dumpxml(vm_name, "--xpath //disk", debug=True).stdout_text
        if "dataStore" not in disk_xml:
            test.fail("Can't get the datastore element automatically!")
        for file in file_list:
            if file not in disk_xml:
                test.fail("Can't get the expected file %s in the domain XML!" % file)
        test.log.info("TEST_STEP: Do read/write for disk.")
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        utils_disk.dd_data_to_vm_disk(vm_session, new_disk)
        vm_session.close()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    disk_dict = eval(params.get('disk_dict', '{}'))
    disk_type = params.get("disk_type")
    with_hotplug = "yes" == params.get("with_hotplug", "no")
    with_block = "yes" == params.get("with_block", "no")
    disk_size = params.get("disk_size")
    disk_image_format = params.get("disk_image_format", "qcow2")
    with_nested = "yes" == params.get("with_nested", "no")
    with_backing_images = "yes" == params.get("with_backing_images", "no")
    libvirt_version.is_libvirt_feature_supported(params)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)
    file_list = []

    try:
        test.log.info("TEST_STEP: Prepare the guest.")
        data_file = prepare_disk()
        file_list.append(data_file)
        data_file_option = params.get("data_file_option") % data_file
        if with_nested:
            active_image_path, data_file_option, disk_dict = prepare_nested_xml(data_file,
                                                                                disk_dict)
        if with_hotplug:
            test.log.info("TEST_STEP: Hotplug disk with datastore.")
            new_image_path, disk_dev = hotplug_disk(data_file_option)
        elif with_nested:
            if with_backing_images:
                disk_dev = libvirt_vmxml.create_vm_device_by_type("disk", disk_dict)
                libvirt.add_vm_device(vmxml, disk_dev)
            else:
                new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict,
                                                      new_image_path=active_image_path)
        else:
            new_image_path = disk_obj.add_vm_disk(disk_type, disk_dict, new_image_path="",
                                                  extra=data_file_option)
        if "new_image_path" in locals():
            file_list.append(new_image_path)
        test.log.info("TEST_STEP: Check the disk dumpxml.")
        check_result()
        if with_hotplug:
            test.log.info("TEST_STEP: Hot-unplug disk.")
            virsh.detach_device(vm_name, disk_dev.xml, debug=True, ignore_status=False,
                                wait_for_event=True)
    finally:
        vmxml_backup.sync()
        if with_block:
            libvirt.setup_or_cleanup_iscsi(is_setup=False)
        for file in file_list:
            if os.path.exists(file):
                os.remove(file)
