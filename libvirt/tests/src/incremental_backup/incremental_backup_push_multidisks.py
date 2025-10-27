import os
import ast

from virttest import virsh
from virttest import data_dir
from virttest import utils_backup
from virttest import utils_disk

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import backup_xml
from virttest.libvirt_xml import checkpoint_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Backup multiple disks by using push mode.
    """
    def create_disk_images(disk_dict, disk_image_list, new_image_path, prepare_disk_xml=False):
        """
        Create disk images for guest.
        :param disk_dict: dict, disk info dict.
        :param disk_image_list: the list of disk images.
        :param new_image_path: path of new image.
        :param prepare_disk_xml: bool, whether to prepare xml or not.
        :return: the tuple of the disk xml and disk images list.
        """
        disk_xml = None
        libvirt.create_local_disk("file", path=new_image_path, size="2048M", disk_format="qcow2")
        if prepare_disk_xml:
            disk_xml, _ = disk_obj.prepare_disk_obj(disk_type, disk_dict, new_image_path)
        else:
            disk_obj.add_vm_disk(disk_type, disk_dict, new_image_path)
        disk_image_list.append(new_image_path)
        return disk_xml, disk_image_list

    def prepare_guest():
        """
        Prepare the guest for backup.

        :return: return the disk images list.
        """
        disk_image_list = []
        disk_dict = ast.literal_eval(params.get("disk_dict", "{}") % "vdb")
        new_image_path = data_dir.get_data_dir() + '/vdb.qcow2'
        _, disk_image_list = create_disk_images(disk_dict, disk_image_list, new_image_path)
        if not with_hotplug:
            disk_dict = ast.literal_eval(params.get("disk_dict", "{}") % "vdc")
            new_image_path = data_dir.get_data_dir() + '/vdc.qcow2'
            _, disk_image_list = create_disk_images(disk_dict, disk_image_list, new_image_path)
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        return disk_image_list

    def write_data():
        """
        Write data to the disk in guest.
        """
        dd_seek = params.get("dd_seek")
        dd_count = params.get("dd_count")
        vm_session = vm.wait_for_login()
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        utils_disk.dd_data_to_vm_disk(vm_session, "/dev/%s" % new_disk, seek=dd_seek, count=dd_count)
        vm_session.close()

    def begin_backup(backup_dict, checkpoint_dict):
        """
        Prepare the backup xml and checkpoint xml and do backup.

        :param backup_dict: The dict contains the backup xml info.
        :param checkpoint_dict: The dict contains the checkpoint xml info.
        """
        backup_dev = backup_xml.BackupXML()
        backup_dev.setup_attrs(**backup_dict)
        test.log.debug("The backup xml is: %s" % backup_dev)
        checkpoint_dev = checkpoint_xml.CheckpointXML()
        checkpoint_dev.setup_attrs(**checkpoint_dict)
        test.log.debug("The checkpoint xml is: %s" % checkpoint_dev)
        backup_options = backup_dev.xml + " " + checkpoint_dev.xml
        write_data()
        virsh.backup_begin(vm_name, backup_options, debug=True, ignore_status=False)
        res = virsh.domjobinfo(vm_name).stdout_text
        if "Backup" not in res:
            test.fail("The Backup operation isn't in the domjobinfo %s as expect." % res)

    def check_bitmaps():
        """
        Check the bitmaps of disk images.
        """
        virsh.destroy(vm_name)
        vdb_bitmaps = utils_backup.get_img_bitmaps(disk_image_list[0])
        if sorted(vdb_bitmaps) != sorted(checkpoint_list):
            test.fail("The bitmaps of disk image %s are not correct." % disk_image_list[0])
        expected_vdc = checkpoint_list[1:]
        vdc_bitmaps = utils_backup.get_img_bitmaps(disk_image_list[1])
        if sorted(vdc_bitmaps) != sorted(expected_vdc):
            test.fail("The bitmaps of disk image %s are not correct." % disk_image_list[1])

    def push_multi_disk_backup(disk_image_list):
        """
        Push mode backup multiple disks in different modes.

        :param disk_image_list: the list of disk images.
        """
        full_backup_dict = ast.literal_eval(params.get("full_backup_dict") % backup_file_list[0])
        inc_backup_dict = ast.literal_eval(params.get("inc_backup_dict") % (backup_file_list[1], backup_file_list[2]))
        diff_backup_dict = ast.literal_eval(params.get("inc_backup_dict") % (backup_file_list[3], backup_file_list[4]))
        backup_configs = [
            (full_backup_dict, ast.literal_eval(params.get("first_checkpoint_dict") % checkpoint_list[0])),
            (inc_backup_dict, ast.literal_eval(params.get("next_checkpoint_dict") % checkpoint_list[1])),
            (diff_backup_dict, ast.literal_eval(params.get("next_checkpoint_dict") % checkpoint_list[2])),
        ]
        for backup_dict, checkpoint_dict in backup_configs:
            if backup_dict == inc_backup_dict:
                disk_dict = ast.literal_eval(params.get("disk_dict") % "vdc")
                new_image_path = data_dir.get_data_dir() + '/vdc.qcow2'
                disk_xml, disk_image_list = create_disk_images(disk_dict, disk_image_list,
                                                               new_image_path, prepare_disk_xml=True)
                virsh.attach_device(vm_name, disk_xml.xml, debug=True, ignore_status=False)
            begin_backup(backup_dict, checkpoint_dict)
        check_bitmaps()

    def push_one_disk_excluded():
        """
        Do backup with one disk not captured by intermediate checkpoint.
        """
        full_backup_dict = ast.literal_eval(params.get("full_backup_dict") % (backup_file_list[0], backup_file_list[1]))
        inc_backup_dict = ast.literal_eval(params.get("inc_backup_dict") % backup_file_list[2])
        diff_backup_dict = ast.literal_eval(params.get("full_backup_dict") % (backup_file_list[3], backup_file_list[4]))
        backup_configs = [
            (full_backup_dict, ast.literal_eval(params.get("first_checkpoint_dict") % checkpoint_list[0])),
            (inc_backup_dict, ast.literal_eval(params.get("next_checkpoint_dict") % checkpoint_list[1])),
            (diff_backup_dict, ast.literal_eval(params.get("first_checkpoint_dict") % checkpoint_list[2])),
        ]
        for backup_dict, checkpoint_dict in backup_configs:
            vm_session = vm.wait_for_login()
            new_disk = libvirt_disk.get_non_root_disk_names(vm_session)[1][0]
            if backup_dict == inc_backup_dict:
                libvirt_disk.check_virtual_disk_io(vm, new_disk, umount=False)
            begin_backup(backup_dict, checkpoint_dict)
        _, output = vm_session.cmd_status_output("ls -l /test")
        if "testfile" in output:
            test.fail("Backup file of vdc shouldn't be existed after incremental backup.")
        vm_session.close()

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    disk_type = params.get("disk_type", "file")
    with_hotplug = "yes" == params.get("with_hotplug", "no")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)
    backup_file_list = []
    disk_image_list = []
    checkpoint_list = ["ck1", "ck2", "ck3"]

    try:
        disk_image_list = prepare_guest()
        for index in range(5):
            file_path = data_dir.get_data_dir() + '/backup_%s' % index
            backup_file_list.append(file_path)
        if with_hotplug:
            push_multi_disk_backup(disk_image_list)
        else:
            push_one_disk_excluded()
    finally:
        for checkpoint_name in checkpoint_list:
            virsh.checkpoint_delete(vm_name, checkpoint_name, '--metadata')
        if vm.is_alive():
            vm.destroy()
        vmxml_backup.sync()
        for file in (disk_image_list + backup_file_list):
            if os.path.exists(file):
                os.remove(file)
