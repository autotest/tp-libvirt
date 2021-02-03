import os
import re
import json
import logging
import shutil

from avocado.utils import process

from virttest import virsh
from virttest import data_dir
from virttest import utils_disk
from virttest import utils_backup
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Integration test of backup and backing_chain.

    Steps:
    1. craete a vm with extra disk vdb
    2. create some data on vdb
    3. start a pull mode full backup on vdb
    4. create some data on vdb
    5. start a pull mode incremental backup
    6. repeat step 5 to 7
    7. before the last round of backup job, do a blockcommit/pull/copy
    8. check the full/incremental backup file data
    """

    def run_blk_cmd():
        """
        Run blockcommit/blockpull/blockcopy command.
        """

        def run_blockpull():
            """
            Run blockpull command.
            """
            if from_to == "mid_to_top":
                cmd_option = ("--base {0}[{1}] --wait").format(original_disk_target,
                                                               middle_layer1_index)
            elif from_to == "base_to_top":
                cmd_option = ("--base {0}[{1}] --wait").format(original_disk_target,
                                                               base_layer_index)
            virsh.blockpull(vm_name, original_disk_target, cmd_option,
                            debug=True, ignore_status=False)

        def run_blockcommit():
            """
            Run blockcommit command.
            """
            if from_to == "top_to_base":
                # Do blockcommit from top layer to base layer
                cmd_option = ("--top {0}[{1}] --base {0}[{2}] --active --pivot "
                              "--wait".format(original_disk_target,
                                              top_layer_index,
                                              base_layer_index)
                              )

            elif from_to == "mid_to_mid":
                # Do blockcommit from middle layer to another middle layer
                if len(indice) < 4:
                    test.fail("At lease 4 layers required for the test 'mid_to_mid'")
                cmd_option = ("--top {0}[{1}] --base {0}[{2}] "
                              "--wait".format(original_disk_target,
                                              middle_layer1_index,
                                              middle_layer2_index)
                              )
            elif from_to == "top_to_mid":
                # Do blockcommit from top layer to middle layer
                cmd_option = ("--top {0}[{1}] --base {0}[{2}] --active --pivot "
                              "--wait".format(original_disk_target,
                                              top_layer_index,
                                              middle_layer1_index)
                              )
            elif from_to == "mid_to_base":
                # Do blockcommit from middle layer to base layer
                cmd_option = ("--top {0}[{1}] --base {0}[{2}] "
                              "--wait".format(original_disk_target,
                                              middle_layer1_index,
                                              base_layer_index)
                              )
            virsh.blockcommit(vm_name, original_disk_target, cmd_option,
                              debug=True, ignore_stauts=False)

        def run_blockcopy():
            """
            Run blockcopy command.
            """
            copy_dest = os.path.join(tmp_dir, "copy_dest.qcow2")
            cmd_option = "--wait --verbose --transient-job --pivot"
            if blockcopy_method == "shallow_copy":
                cmd_option += " --shallow"
            if blockcopy_reuse == "reuse_external":
                cmd_option += " --reuse-external"
                if blockcopy_method == "shallow_copy":
                    create_img_cmd = "qemu-img create -f qcow2 -F qcow2 -b %s %s"
                    create_img_cmd %= (backend_img, copy_dest)
                else:
                    create_img_cmd = "qemu-img create -f qcow2 %s %s"
                    create_img_cmd %= (copy_dest, original_disk_size)
                process.run(create_img_cmd, shell=True, ignore_status=False)
            virsh.blockcopy(vm_name, original_disk_target, copy_dest, cmd_option,
                            debug=True, ignore_status=False)

        # Get disk backing store indice info in vm disk xml
        cur_vm_xml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        cur_disk_xmls = cur_vm_xml.get_devices(device_type="disk")
        cur_test_disk_xml = ''
        for disk_xml in cur_disk_xmls:
            if disk_xml.target['dev'] == original_disk_target:
                cur_test_disk_xml = disk_xml
                logging.debug("Current disk xml for %s is:\n %s",
                              original_disk_target, cur_test_disk_xml)
                break
        indice = re.findall(r".*index=['|\"](\d+)['|\"].*", str(cur_test_disk_xml))
        logging.debug("backing store indice for %s is: %s",
                      original_disk_target, indice)
        if len(indice) < 3:
            test.fail("At least 3 layers required for the test.")
        top_layer_index = indice[0]
        middle_layer1_index = indice[1]
        middle_layer2_index = indice[-2]
        base_layer_index = indice[-1]
        logging.debug("Following backing store will be used: %s",
                      "top:%s; middle_1: %s, middle_2:%s, base: %s"
                      % (top_layer_index, middle_layer1_index,
                         middle_layer2_index, base_layer_index)
                      )
        # Start the block command
        if blockcommand == "blockpull":
            run_blockpull()
        if blockcommand == "blockcommit":
            run_blockcommit()
        if blockcommand == "blockcopy":
            run_blockcopy()

    def create_shutoff_snapshot(original_img, snapshot_img):
        """
        Create shutoff snapshot, which means the disk snapshot is not controlled
        by libvirt, but created directly by qemu command.

        :param original_img: The image we will take shutoff snapshot for.
        :param snapshot_img: The newly created shutoff snapshot image.
        """
        cmd = "qemu-img info --output=json -f qcow2 {}".format(original_img)
        img_info = process.run(cmd, shell=True, ignore_status=False).stdout_text
        json_data = json.loads(img_info)
        cmd = "qemu-img create -f qcow2 -F qcow2 -b {0} {1}".format(original_img, snapshot_img)
        process.run(cmd, shell=True, ignore_status=False)
        try:
            bitmaps = json_data['format-specific']['data']['bitmaps']
            for bitmap in bitmaps:
                bitmap_flags = bitmap['flags']
                bitmap_name = bitmap['name']
                if 'auto' in bitmap_flags and 'in-use' not in bitmap_flags:
                    cmd = "qemu-img bitmap -f qcow2 {0} --add {1}".format(snapshot_img, bitmap_name)
                    process.run(cmd, shell=True, ignore_status=False)
        except Exception as bitmap_error:
            logging.debug("Cannot add bitmap to new image, skip it: %s",
                          bitmap_error)

    # Cancel the test if libvirt version is too low
    if not libvirt_version.version_compare(6, 0, 0):
        test.cancel("Current libvirt version doesn't support "
                    "incremental backup.")

    # vm's origianl disk config
    original_disk_size = params.get("original_disk_size", "100M")
    original_disk_type = params.get("original_disk_type", "local")
    original_disk_target = params.get("original_disk_target", "vdb")

    # pull mode backup config
    scratch_type = params.get("scratch_type", "file")
    nbd_protocol = params.get("nbd_protocol", "tcp")
    nbd_tcp_port = params.get("nbd_tcp_port", "10809")

    # test config
    backup_rounds = int(params.get("backup_rounds", 4))
    shutoff_snapshot = "yes" == params.get("shutoff_snapshot")
    blockcommand = params.get("blockcommand")
    from_to = params.get("from_to")
    blockcopy_method = params.get("blockcopy_method")
    blockcopy_reuse = params.get("blockcopy_reuse")
    backup_error = "yes" == params.get("backup_error")
    tmp_dir = data_dir.get_tmp_dir()

    try:
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)

        # Make sure there is no checkpoint metadata before test
        utils_backup.clean_checkpoints(vm_name)

        # Backup vm xml
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()
        disks_not_tested = list(vmxml.get_disk_all().keys())
        logging.debug("Not tested disks are: %s", disks_not_tested)
        utils_backup.enable_inc_backup_for_vm(vm)

        # Destroy vm before test
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Prepare the disk to be backuped.
        disk_params = {}
        disk_path = ""
        if original_disk_type == "local":
            image_name = "%s_image.qcow2" % original_disk_target
            disk_path = os.path.join(tmp_dir, image_name)
            libvirt.create_local_disk("file", disk_path, original_disk_size,
                                      "qcow2")
            disk_params = {"device_type": "disk",
                           "type_name": "file",
                           "driver_type": "qcow2",
                           "target_dev": original_disk_target,
                           "source_file": disk_path}
            if original_disk_target:
                disk_params["target_dev"] = original_disk_target
        else:
            logging.cancel("The disk type '%s' not supported in this script.",
                           original_disk_type)
        disk_xml = libvirt.create_disk_xml(disk_params)
        virsh.attach_device(vm.name, disk_xml,
                            flagstr="--config", debug=True)
        vm.start()
        session = vm.wait_for_login()
        new_disks_in_vm = list(utils_disk.get_linux_disks(session).keys())
        session.close()
        if len(new_disks_in_vm) != 1:
            test.fail("Test disk not prepared in vm")

        # Use the newly added disk as the test disk
        test_disk_in_vm = "/dev/" + new_disks_in_vm[0]

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_disks = list(vmxml.get_disk_all().keys())

        checkpoint_list = []
        is_incremental = False
        backup_file_list = []
        snapshot_list = []
        cur_disk_xml = disk_xml
        cur_disk_path = disk_path
        cur_disk_params = disk_params
        backend_img = ""
        for backup_index in range(backup_rounds):
            # Do external snapshot
            if shutoff_snapshot:
                virsh.detach_disk(vm.name, original_disk_target,
                                  extra="--persistent",
                                  ignore_status=False, debug=True)
                if vm.is_alive():
                    vm.destroy(gracefully=False)
                shutoff_snapshot_name = "shutoff_snap_%s" % str(backup_index)
                shutoff_snapshot_path = os.path.join(tmp_dir, shutoff_snapshot_name)

                create_shutoff_snapshot(cur_disk_path, shutoff_snapshot_path)
                cur_disk_params["source_file"] = shutoff_snapshot_path
                cur_disk_xml = libvirt.create_disk_xml(cur_disk_params)
                virsh.attach_device(vm.name, cur_disk_xml,
                                    flagstr="--config",
                                    ignore_status=False,
                                    debug=True)
                vm.start()
                vm.wait_for_login().close()
                cur_disk_path = shutoff_snapshot_path
            else:
                snapshot_name = "snap_%s" % str(backup_index)
                snapshot_option = ""
                snapshot_file_name = os.path.join(tmp_dir, snapshot_name)
                for disk_name in disks_not_tested:
                    snapshot_option += "--diskspec %s,snapshot=no " % disk_name
                snapshot_option += "--diskspec %s,file=%s" % (original_disk_target,
                                                              snapshot_file_name)
                virsh.snapshot_create_as(vm_name,
                                         "%s --disk-only %s" % (snapshot_name,
                                                                snapshot_option),
                                         debug=True)
                snapshot_list.append(snapshot_name)

            # Prepare backup xml
            backup_params = {"backup_mode": "pull"}
            if backup_index > 0:
                is_incremental = True
                backup_params["backup_incremental"] = "checkpoint_" + str(backup_index - 1)

            # Set libvirt default nbd export name and bitmap name
            nbd_export_name = original_disk_target
            nbd_bitmap_name = "backup-" + original_disk_target

            backup_server_dict = {"name": "localhost",
                                  "port": nbd_tcp_port}
            backup_params["backup_server"] = backup_server_dict
            backup_disk_xmls = []
            for vm_disk in vm_disks:
                backup_disk_params = {"disk_name": vm_disk}
                if vm_disk != original_disk_target:
                    backup_disk_params["enable_backup"] = "no"
                else:
                    backup_disk_params["enable_backup"] = "yes"
                    backup_disk_params["disk_type"] = scratch_type

                    # Prepare nbd scratch file/dev params
                    scratch_params = {"attrs": {}}
                    scratch_file_name = "scratch_file_%s" % backup_index
                    scratch_file_path = os.path.join(tmp_dir, scratch_file_name)
                    scratch_params["attrs"]["file"] = scratch_file_path
                    logging.debug("scratch_params: %s", scratch_params)
                    backup_disk_params["backup_scratch"] = scratch_params
                backup_disk_xml = utils_backup.create_backup_disk_xml(
                        backup_disk_params)
                backup_disk_xmls.append(backup_disk_xml)
            logging.debug("disk list %s", backup_disk_xmls)
            backup_xml = utils_backup.create_backup_xml(backup_params,
                                                        backup_disk_xmls)
            logging.debug("ROUND_%s Backup Xml: %s", backup_index, backup_xml)

            # Prepare checkpoint xml
            checkpoint_name = "checkpoint_%s" % backup_index
            checkpoint_list.append(checkpoint_name)
            cp_params = {"checkpoint_name": checkpoint_name}
            cp_params["checkpoint_desc"] = params.get("checkpoint_desc",
                                                      "desc of cp_%s" % backup_index)
            disk_param_list = []
            for vm_disk in vm_disks:
                cp_disk_param = {"name": vm_disk}
                if vm_disk != original_disk_target:
                    cp_disk_param["checkpoint"] = "no"
                else:
                    cp_disk_param["checkpoint"] = "bitmap"
                    cp_disk_bitmap = params.get("cp_disk_bitmap")
                    if cp_disk_bitmap:
                        cp_disk_param["bitmap"] = cp_disk_bitmap + str(backup_index)
                disk_param_list.append(cp_disk_param)
            checkpoint_xml = utils_backup.create_checkpoint_xml(cp_params,
                                                                disk_param_list)
            logging.debug("ROUND_%s Checkpoint Xml: %s",
                          backup_index, checkpoint_xml)

            # Start backup
            backup_options = backup_xml.xml + " " + checkpoint_xml.xml

            # Create some data in vdb
            dd_count = "1"
            dd_seek = str(backup_index * 10 + 10)
            dd_bs = "1M"
            session = vm.wait_for_login()
            utils_disk.dd_data_to_vm_disk(session, test_disk_in_vm, dd_bs,
                                          dd_seek, dd_count)
            session.close()

            backup_result = virsh.backup_begin(vm_name, backup_options,
                                               debug=True)
            if backup_result.exit_status:
                raise utils_backup.BackupBeginError(backup_result.stderr.strip())

            backup_file_path = os.path.join(
                    tmp_dir, "backup_file_%s.qcow2" % str(backup_index))
            backup_file_list.append(backup_file_path)
            nbd_params = {"nbd_protocol": nbd_protocol,
                          "nbd_hostname": "localhost",
                          "nbd_export": nbd_export_name,
                          "nbd_tcp_port": nbd_tcp_port}
            if not is_incremental:
                # Do full backup
                utils_backup.pull_full_backup_to_file(nbd_params,
                                                      backup_file_path)
                logging.debug("Full backup to: %s", backup_file_path)
            else:
                # Do incremental backup
                utils_backup.pull_incremental_backup_to_file(
                        nbd_params, backup_file_path, nbd_bitmap_name,
                        original_disk_size)
            virsh.domjobabort(vm_name, debug=True)
            # Start to run the blockcommit/blockpull cmd before the last round
            # of backup job, this is to test if the block command will keep the
            # dirty bitmap data.
            if backup_index == backup_rounds - 2:
                run_blk_cmd()
                cur_disk_path = vm.get_blk_devices()[original_disk_target]['source']

            if backup_index == backup_rounds - 3:
                backend_img = vm.get_blk_devices()[original_disk_target]['source']

        # Get current active image for the test disk
        vm_disks = vm.get_blk_devices()
        current_active_image = vm_disks[original_disk_target]['source']
        logging.debug("The current active image for '%s' is '%s'",
                      original_disk_target, current_active_image)

        for checkpoint_name in checkpoint_list:
            virsh.checkpoint_delete(vm_name, checkpoint_name,
                                    debug=True, ignore_status=False)
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Compare the backup data and original data

        original_data_file = os.path.join(tmp_dir, "original_data.qcow2")
        cmd = "qemu-img convert -f qcow2 %s -O qcow2 %s" % (current_active_image, original_data_file)
        process.run(cmd, shell=True, verbose=True)
        for backup_file in backup_file_list:
            if not utils_backup.cmp_backup_data(original_data_file, backup_file):
                test.fail("Backup and original data are not identical for"
                          "'%s' and '%s'" % (current_active_image, backup_file))
            else:
                logging.debug("'%s' contains correct backup data", backup_file)
    except utils_backup.BackupBeginError as details:
        if backup_error:
            logging.debug("Backup failed as expected.")
        else:
            test.fail(details)
    finally:
        # Remove checkpoints' metadata again to make sure vm has no checkpoints
        if "checkpoint_list" in locals():
            for checkpoint_name in checkpoint_list:
                virsh.checkpoint_delete(vm_name, checkpoint_name,
                                        options="--metadata")
        # Remove snapshots
        if "snapshot_list" in locals():
            for snapshot_name in snapshot_list:
                virsh.snapshot_delete(vm_name, "%s --metadata" % snapshot_name,
                                      debug=True)

        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Restoring vm
        vmxml_backup.sync()

        for file_name in os.listdir(tmp_dir):
            file_path = os.path.join(tmp_dir, file_name)
            if 'env' not in file_path:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
