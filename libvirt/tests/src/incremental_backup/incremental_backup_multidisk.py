import os
import logging

from virttest import virsh
from virttest import data_dir
from virttest import utils_backup
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test multiple disks' backup function

    Steps:
    1. Attach disk_1 to vm
    2. Do full backup of this disk
    3. Attach disk_2 to vm
    4. Do incremental backup for disk_1 and full backup for disk_2
    5. Repeat step 3~4 if having more test disks
    """

    def get_disks_need_backup(disk_dict):
        """
        Get the disks which need to be backuped

        :param disk_list: All test disks
        :return: Disks need to be backuped
        """
        disks = []
        for disk in list(disk_dict.keys()):
            if disk_dict[disk]['is_attached']:
                disks.append(disk)
        return disks

    # Cancel the test if libvirt version is too low
    if not libvirt_version.version_compare(6, 6, 0):
        test.cancel("Current libvirt version doesn't support "
                    "mixed full/incremental backup.")

    # Basic test setting
    test_disk_size = params.get("test_disk_size", "100M")
    total_test_disk = int(params.get("total_test_disk", 3))
    tmp_dir = data_dir.get_tmp_dir()
    backup_error = "yes" == params.get("backup_error")

    # Backup setting
    scratch_type = params.get("scratch_type", "file")
    nbd_protocol = params.get("nbd_protocol", "tcp")
    nbd_tcp_port = params.get("nbd_tcp_port", "10809")
    set_export_name = "yes" == params.get("set_export_name")
    set_export_bitmap = "yes" == params.get("set_export_bitmap")

    try:
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)

        # Make sure there is no checkpoint metadata before test
        utils_backup.clean_checkpoints(vm_name)

        # Backup vm xml
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()
        utils_backup.enable_inc_backup_for_vm(vm)

        # Prepare a dict to save the disks' info to be attached and backuped.
        # This will generate a dict as:
        # {'vdb': {'path': '', 'is_attached': False, 'checkpoints': []},
        #  'vdc': {'path': '', 'is_attached': False, 'checkpoints': []}}
        test_disk_prefix = 'vd'
        test_disk_sequence = 'b'
        test_disk_dict = {}
        for i in range(total_test_disk):
            test_disk_name = test_disk_prefix + chr(i + ord(test_disk_sequence))
            test_disk_dict[test_disk_name] = {'path': '',
                                              'is_attached': False,
                                              'checkpoints': []}

        if not vm.is_alive():
            vm.start()
            vm.wait_for_login().close()

        # Start to prepare backup and checkpoint xml
        checkpoint_round = 0
        checkpoint_list = []
        backup_params = {'backup_mode': 'pull'}
        backup_server_dict = {}
        if nbd_protocol == 'tcp':
            backup_server_dict['name'] = 'localhost'
            backup_server_dict['port'] = nbd_tcp_port
        else:
            test.cancel("We only test nbd export via tcp/ip fow now.")
        backup_params["backup_server"] = backup_server_dict
        test_disk_list = list(test_disk_dict.keys())

        def prepare_disk_img(test_disk):
            """
            Prepare a qcow2 image for test disk

            :param test_disk: The vm's disk, such as 'vdb'
            :return: The path to the image
            """
            image_name = "{}_image.qcow2".format(test_disk)
            image_path = os.path.join(tmp_dir, image_name)
            libvirt.create_local_disk("file", image_path, test_disk_size,
                                      "qcow2")
            return image_path

        def prepare_disk_xml(test_disk, image_path):
            """
            Prepare disk xml to be hot plugged

            :param test_disk: The vm's disk, such as 'vdb'
            :param image_path: The path to the iamge
            :return: The xml of the test disk
            """
            test_disk_params = {"device_type": "disk",
                                "type_name": "file",
                                "driver_type": "qcow2",
                                "target_dev": test_disk,
                                "source_file": image_path,
                                "target_bus": "scsi"}
            test_disk_xml = libvirt.create_disk_xml(test_disk_params)
            return test_disk_xml

        def prepare_backup_xml(backup_disks, all_vm_disks):
            """
            Prepare backup xml

            :param backup_disks: List of the disks to be backuped
            :param all_vm_disks: List of all the vm disks
            :return: Backup xml
            """
            backup_disk_xmls = []
            for vm_disk in all_vm_disks:
                backup_disk_params = {"disk_name": vm_disk}
                if vm_disk not in backup_disks:
                    backup_disk_params["enable_backup"] = "no"
                else:
                    backup_disk_params["enable_backup"] = "yes"
                    backup_disk_params["disk_type"] = scratch_type
                    # Custom nbd export name and bitmap name if required
                    if set_export_name:
                        nbd_export_name = vm_disk + "_custom_exp"
                        backup_disk_params["exportname"] = nbd_export_name
                    if set_export_bitmap:
                        nbd_bitmap_name = vm_disk + "_custom_bitmap"
                        backup_disk_params["exportbitmap"] = nbd_bitmap_name
                    # Prepare nbd scratch file params
                    scratch_params = {"attrs": {}}
                    if scratch_type == "file":
                        scratch_file_name = "scratch_file_%s" % vm_disk
                        scratch_file_path = os.path.join(tmp_dir, scratch_file_name)
                        scratch_params["attrs"]["file"] = scratch_file_path
                        logging.debug("scratch_params: %s", scratch_params)
                    else:
                        test.cancel("We only use local file scratch for now.")
                    backup_disk_params["backup_scratch"] = scratch_params
                    # Prepare 'backupmode' and 'incremental' attributes
                    if test_disk_dict[vm_disk]['checkpoints']:
                        backup_disk_params['backupmode'] = 'incremental'
                        backup_disk_params['incremental'] = test_disk_dict[vm_disk]['checkpoints'][-1]
                    else:
                        backup_disk_params['backupmode'] = 'full'
                backup_disk_xml = utils_backup.create_backup_disk_xml(
                        backup_disk_params)
                backup_disk_xmls.append(backup_disk_xml)
            logging.debug("disk list %s", backup_disk_xmls)
            backup_xml = utils_backup.create_backup_xml(backup_params,
                                                        backup_disk_xmls)
            return backup_xml

        def prepare_checkpoint_xml(backup_disks, all_vm_disks):
            """
            Preapre checkpoint xml

            :param backup_disks: List of disks to be backuped
            :param all_vm_disks: List of vm disks
            :return: checkpoint name and checkpoint xml
            """
            checkpoint_name = "checkpoint_%s" % str(checkpoint_round)
            cp_params = {"checkpoint_name": checkpoint_name}
            cp_params["checkpoint_desc"] = params.get("checkpoint_desc",
                                                      "desc of cp_%s" % str(checkpoint_round))
            disk_param_list = []
            for vm_disk in all_vm_disks:
                cp_disk_param = {"name": vm_disk}
                if vm_disk not in backup_disks:
                    cp_disk_param["checkpoint"] = "no"
                else:
                    test_disk_dict[vm_disk]['checkpoints'].append(checkpoint_name)
                    cp_disk_param["checkpoint"] = "bitmap"
                    cp_disk_bitmap = params.get("cp_disk_bitmap")
                    if cp_disk_bitmap:
                        cp_disk_param["bitmap"] = cp_disk_bitmap + str(checkpoint_round)
                disk_param_list.append(cp_disk_param)
            checkpoint_xml = utils_backup.create_checkpoint_xml(cp_params,
                                                                disk_param_list)
            return checkpoint_name, checkpoint_xml

        for test_disk in test_disk_list:
            if checkpoint_list:
                enable_incremental_backup = True
                backup_params["backup_incremental"] = checkpoint_list[-1]
            # Prepare disk image
            image_path = prepare_disk_img(test_disk)
            # Prepare disk xml to be hotplugged
            test_disk_xml = prepare_disk_xml(test_disk, image_path)
            # Hotplug disk
            virsh.attach_device(vm_name, test_disk_xml, debug=True,
                                ignore_status=False)
            test_disk_dict[test_disk]['path'] = image_path
            test_disk_dict[test_disk]['is_attached'] = True
            # Now we use attached disk as backup disks
            backup_disks = get_disks_need_backup(test_disk_dict)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            all_vm_disks = list(vmxml.get_disk_all().keys())
            # Prepare backup xml
            backup_xml = prepare_backup_xml(backup_disks, all_vm_disks)
            logging.debug("ROUND_%s Backup xml: %s",
                          checkpoint_round, backup_xml)
            # Prepare checkpoint xml
            checkpoint_name, checkpoint_xml = prepare_checkpoint_xml(backup_disks,
                                                                     all_vm_disks)
            logging.debug("ROUND_%s Checkpoint Xml: %s",
                          checkpoint_round, checkpoint_xml)
            # Start backup job
            backup_options = backup_xml.xml + " " + checkpoint_xml.xml
            virsh.backup_begin(vm_name, backup_options, debug=True,
                               ignore_status=False)
            # Abort backup job
            virsh.domjobabort(vm_name, debug=True, ignore_status=False)

            checkpoint_list.append(checkpoint_name)
            checkpoint_round += 1

        # Destroy vm to make sure correct info can be read from images
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Check if images' bitmap info is same as the checkponits we created
        bitmaps_eq_checkponits = True
        for test_disk in list(test_disk_dict.keys()):
            bitmaps = utils_backup.get_img_bitmaps(test_disk_dict[test_disk]['path'])
            if sorted(bitmaps) != sorted(test_disk_dict[test_disk]['checkpoints']):
                bitmaps_eq_checkponits = False
                logging.error("%s(%s): checkpoints %s created by libvirt,"
                              "but bitmaps %s can be found by qemu-img info."
                              % (test_disk, test_disk_dict[test_disk]['path'],
                                 test_disk_dict[test_disk]['checkpoints'],
                                 bitmaps))
        if not bitmaps_eq_checkponits:
            test.fail("The checkponits created by libvirt are not same as "
                      "the bitmaps created in qemu image, detailed info can "
                      "be found with 'logging.error'.")
    except utils_backup.BackupBeginError as details:
        if backup_error:
            logging.debug("Backup failed as expected.")
        else:
            test.fail(details)
    finally:
        # Remove checkpoints. Since the OS image not touched during the test,
        # we only need to remove the checkpoints' metadata here.
        if "checkpoint_list" in locals() and checkpoint_list:
            for checkpoint_name in checkpoint_list:
                virsh.checkpoint_delete(vm_name, checkpoint_name, '--metadata')

        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Restoring vm
        vmxml_backup.sync()
