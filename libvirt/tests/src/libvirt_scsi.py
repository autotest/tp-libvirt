"""
libvirt scsi test

@copyright: 2013 FNST Inc.
"""
import os

from avocado.utils import process

from virttest import virt_vm
from virttest import qemu_storage
from virttest import data_dir
from virttest.utils_disk import CdromDisk
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.xcepts import LibvirtXMLError


def run(test, params, env):
    # Get variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    img_type = ('yes' == params.get("libvirt_scsi_img_type", "no"))
    cdrom_type = ('yes' == params.get("libvirt_scsi_cdrom_type", "no"))
    partition_type = ('yes' == params.get("libvirt_scsi_partition_type", "no"))
    partition = params.get("libvirt_scsi_partition",
                           "ENTER.YOUR.AVAILABLE.PARTITION")
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    # Init a VM instance and a VMXML instance.
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    # Keep a backup of xml to restore it in cleanup.
    backup_xml = vmxml.copy()
    # Add a scsi controller if there is not.
    controller_devices = vmxml.get_devices("controller")
    scsi_controllers = []
    for device in controller_devices:
        if device.type == "scsi":
            scsi_controllers.append(device)

    if not scsi_controllers:
        scsi_controller = Controller("controller")
        scsi_controller.type = "scsi"
        scsi_controller.index = "0"
        scsi_controller.model = "virtio-scsi"
        vmxml.add_device(scsi_controller)

    # Add disk with bus of scsi into vmxml.
    if partition_type:
        if partition.count("ENTER.YOUR"):
            test.cancel("Partition for partition test "
                        "is not configured.")
        partition_disk = Disk(type_name="block")
        partition_disk.device = "disk"
        partition_disk.target = {'dev': "vdg",
                                 'bus': "scsi"}
        partition_disk.source = partition_disk.new_disk_source(
            **{'attrs': {'dev': partition}})
        vmxml.add_device(partition_disk)
    if img_type:
        # Init a QemuImg instance.
        img_name = "libvirt_scsi"
        params['image_name'] = img_name
        image = qemu_storage.QemuImg(params, data_dir.get_tmp_dir(), img_name)
        # Create a image.
        img_path, _ = image.create(params)
        img_disk = Disk(type_name="file")
        img_disk.device = "disk"
        img_disk.source = img_disk.new_disk_source(
            **{'attrs': {'file': img_path}})
        img_disk.target = {'dev': "vde",
                           'bus': "scsi"}
        vmxml.add_device(img_disk)
    if cdrom_type:
        # Init a CdromDisk instance.
        cdrom_path = os.path.join(data_dir.get_tmp_dir(), "libvirt_scsi")
        try:
            cdrom = CdromDisk(cdrom_path, data_dir.get_tmp_dir())
            cdrom.close()
        except process.CmdError as detail:
            test.cancel("Failed to create cdrom disk: %s" % detail)

        cdrom_disk = Disk(type_name="file")
        cdrom_disk.device = "cdrom"
        cdrom_disk.target = {'dev': "vdf",
                             'bus': "scsi"}
        cdrom_disk.source = cdrom_disk.new_disk_source(
            **{'attrs': {'file': cdrom_path}})
        vmxml.add_device(cdrom_disk)
    # Check the result of scsi disk.
    try:
        # sync the vmxml with VM.
        try:
            vmxml.sync()
        except LibvirtXMLError as e:
            test.fail("Test failed in vmxml.sync(), detail:%s." % e)

        try:
            vm.start()
            # Start VM successfully.
            if status_error:
                test.fail('Starting VM succeeded in negative case.')
        except virt_vm.VMStartError as e:
            # Starting VM failed.
            if not status_error:
                test.fail("Test failed in positive case."
                          "error: %s" % e)
    finally:
        if img_type:
            if os.path.isfile(img_path):
                os.remove(img_path)
        if cdrom_type:
            if os.path.isfile(cdrom_path):
                os.remove(cdrom_path)
        # clean up.
        backup_xml.sync()
