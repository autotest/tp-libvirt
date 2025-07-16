import os
from avocado.utils import process

from virttest import virsh
from virttest import utils_backup
from virttest import utils_disk
from virttest import data_dir
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import backup_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Get info during pull mode backup.

    1) Prepare a running guest with two disks.
    2) Write datas to disk by dd.
    3) Start the backup job.
    4) Check related information.
    """
    def prepare_guest():
        """
        Prepare a running guest with two disks.

        :params return: return the tuple of new created images.
        """
        data_file = data_dir.get_data_dir() + '/datastore'
        if disk_type == "file":
            new_image_path = data_dir.get_data_dir() + '/test.img'
            if with_data_file:
                data_file_option = params.get("data_file_option", "") % data_file
            extra_cmd = "" if not with_data_file else data_file_option
            libvirt.create_local_disk(
                        "file", path=new_image_path, size="500M",
                        disk_format="qcow2", extra=extra_cmd)
        else:
            new_image_path = libvirt.setup_or_cleanup_iscsi(is_setup=True)
            cmd = "qemu-img create -f qcow2 %s 500M" % new_image_path
            process.run(cmd, shell=True, ignore_status=False)
        image_path = disk_obj.add_vm_disk(disk_type, disk_dict, new_image_path)
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test.log.debug("The current guest xml is %s", virsh.dumpxml(vm_name).stdout_text)
        return data_file

    def write_datas(dd_seek):
        """
        Write datas to the disk in guest.
        """
        vm_session = vm.wait_for_login()
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        utils_disk.dd_data_to_vm_disk(vm_session, "/dev/%s" % new_disk, seek=dd_seek, count="200")
        vm_session.close()

    def prepare_backup_xml():
        """
        Prepare the backup xml.

        :params return: return the backup options and the scratch file.
        """
        if domblkinfo_check:
            scratch_file = data_dir.get_data_dir() + '/scratch_file'
        else:
            scratch_file = libvirt.setup_or_cleanup_iscsi(is_setup=True)
        backup_dict = eval(params.get("backup_dict") % scratch_file)
        backup_dev = backup_xml.BackupXML()
        backup_dev.setup_attrs(**backup_dict)
        test.log.debug("The backup xml is %s", backup_dev)
        backup_options = backup_dev.xml
        return backup_options, scratch_file

    def check_event():
        """
        Check the event.
        """
        event_session = virsh.EventTracker.start_get_event(vm_name)
        write_datas(dd_seek)
        event_output = virsh.EventTracker.finish_get_event(event_session)
        for line in event_output.splitlines():
            if "block-threshold" in line and threshold_value in line:
                return line
        test.fail("Can't find expected threshold event, but got %s" % event_output)

    def check_scratch_info(scratch_device):
        """
        Check scratch info during pull mode backup.

        :params scratch_device: the scratch device for scratch file.
        """
        test.log.debug("TEST STEP: check the scratch file info after backup.")
        backing_index = None
        first_domstats_output = virsh.domstats(vm_name, "--block --backing", debug=True)
        for line in first_domstats_output.stdout_text.splitlines():
            if scratch_device in line:
                block_index = line.split(".")[1]
                for next_line in first_domstats_output.stdout_text.splitlines():
                    if f"{block_index}.backingIndex" in next_line:
                        backing_index = int(next_line.split("=")[-1])
        if not backing_index:
            test.fail("Can't find the scratch device info!")

        test.log.debug("TEST STEP: check the scratch file info after domblkthreshold.")
        virsh.domblkthreshold(vm_name, '%s[%s]' % (target_disk, backing_index),
                              threshold_value, debug=True, ignore_status=False)
        check_event()
        second_domstats_output = virsh.domstats(vm_name, "--block --backing", debug=True)
        block_allocatoin = 0
        for line in second_domstats_output.stdout_text.splitlines():
            if f"{block_index}.allocation" in line:
                block_allocation = int(line.split("=")[-1])
                if target_min <= block_allocation <= target_max:
                    test.log.debug("The block allocation is %s which is around %s, and between %s"
                                   " and %s." % (block_allocation, target_size, target_min, target_max))
                else:
                    test.fail("The block allocation %s is incorrect!" % block_allocation)

        test.log.debug("TEST STEP: check the scratch file info after domjobabort.")
        virsh.domjobabort(vm_name, debug=True, ignore_status=False)
        three_domstats_output = virsh.domstats(vm_name, "--block --backing", debug=True)
        if scratch_device in three_domstats_output.stdout_text:
            test.fail("The scratch device is still existed which is not expected!")

    def check_domblk_info(target_min, target_max):
        """
        Check the domblk info.

        :params target_min: the min value of the block allocation
        :params target_max: the max value of the block allocation
        """
        test.log.debug("TEST STEP: check the domblk info")
        domblk_output = virsh.domblkinfo(vm_name, target_disk, debug=True)
        block_allocation = 0
        for line in domblk_output.stdout_text.splitlines():
            if "Allocation" in line:
                block_allocation = int(line.split(":")[-1].strip())
        if not (target_min <= block_allocation <= target_max):
            test.fail("The block allocation %s is not expected! It's not between %s and %s."
                      % (block_allocation, target_min, target_max))

    target_disk = params.get("target_disk")
    disk_type = params.get("disk_type")
    dd_count = int(params.get("dd_count"))
    with_data_file = "yes" == params.get("with_data_file", "no")
    data_file_option = params.get("data_file_option")
    threshold_value = params.get("threshold_value")
    disk_dict = eval(params.get("disk_dict", "{}"))
    domblkinfo_check = "yes" == params.get("domblkinfo_check", "no")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    disk_obj = disk_base.DiskBase(test, vm, params)

    target_size = float(dd_count * 1024 * 1024)
    target_min = target_size - target_size * 0.1
    target_max = target_size + target_size * 0.1
    dd_seek = "0"

    try:
        utils_backup.clean_checkpoints(vm_name)
        test.log.debug("TEST STEP1: prepare a running guest.")
        data_file = prepare_guest()
        test.log.debug("TEST STEP2: write datas to the guest disk.")
        write_datas(dd_seek)
        if domblkinfo_check:
            check_domblk_info(target_min, target_max)
        test.log.debug("TEST STEP3: prepare the backup xml.")
        backup_options, scratch_device = prepare_backup_xml()
        test.log.debug("TEST STEP4: start the backup job.")
        backup_result = virsh.backup_begin(vm_name, backup_options,
                                           debug=True, ignore_status=False)
        if backup_result.exit_status:
            raise utils_backup.BackupBeginError(backup_result.stderr.strip())
        if not domblkinfo_check:
            check_scratch_info(scratch_device)
        else:
            check_domblk_info(target_min, target_max)
            write_datas(dd_seek="300")
            check_domblk_info(target_min * 2, target_max * 2)
            virsh.domjobabort(vm_name, debug=True, ignore_status=False)
            check_domblk_info(target_min * 2, target_max * 2)

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        iscsi_target = libvirt.setup_or_cleanup_iscsi(is_setup=False)
        if disk_type == "file":
            disk_obj.cleanup_disk_preparation(disk_type)
        for file in [scratch_device, data_file]:
            if os.path.exists(file):
                os.remove(file)
