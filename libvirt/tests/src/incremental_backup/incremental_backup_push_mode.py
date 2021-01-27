import os
import logging
import xml.etree.ElementTree as ET

from avocado.utils import process

from virttest import virsh
from virttest import ceph
from virttest import data_dir
from virttest import utils_disk
from virttest import utils_backup
from virttest import utils_package
from virttest import utils_misc
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test push-mode incremental backup

    Steps:
    1. create a vm with extra disk vdb
    2. create some data on vdb in vm
    3. start a push mode full backup on vdb
    4. create some data on vdb in vm
    5. start a push mode incremental backup
    6. repeat step 4 and 5 as required
    7. check the full/incremental backup file data
    """

    def backup_job_done(vm_name, vm_disk):
        """
        Check if a backup job for a vm's specific disk is finished.

        :param vm_name: vm's name
        :param vm_disk: the disk to be checked, such as 'vdb'
        :return: 'True' means job finished
        """
        result = virsh.blockjob(vm_name, vm_disk, debug=True)
        if "no current block job" in result.stdout_text.strip().lower():
            return True

    # Cancel the test if libvirt version is too low
    if not libvirt_version.version_compare(6, 0, 0):
        test.cancel("Current libvirt version doesn't support "
                    "incremental backup.")

    hotplug_disk = "yes" == params.get("hotplug_disk", "no")
    original_disk_size = params.get("original_disk_size", "100M")
    original_disk_type = params.get("original_disk_type", "local")
    original_disk_target = params.get("original_disk_target", "vdb")
    target_driver = params.get("target_driver", "qcow2")
    target_type = params.get("target_type", "file")
    target_blkdev_path = params.get("target_blkdev_path")
    target_blkdev_size = params.get("target_blkdev_size", original_disk_size)
    reuse_target_file = "yes" == params.get("reuse_target_file")
    prepare_target_file = "yes" == params.get("prepare_target_file")
    prepare_target_blkdev = "yes" == params.get("prepare_target_blkdev")
    backup_rounds = int(params.get("backup_rounds", 3))
    backup_error = "yes" == params.get("backup_error")
    tmp_dir = data_dir.get_tmp_dir()
    virsh_dargs = {'debug': True, 'ignore_status': True}

    try:
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)

        # Make sure there is no checkpoint metadata before test
        utils_backup.clean_checkpoints(vm_name)

        # Backup vm xml
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()

        # Enable vm incremental backup capability. This is only a workaround
        # to make sure incremental backup can work for the vm. Code needs to
        # be removded immediately when the function enabled by default, which
        # is tracked by bz1799015
        tree = ET.parse(vmxml.xml)
        root = tree.getroot()
        for elem in root.iter('domain'):
            elem.set('xmlns:qemu', 'http://libvirt.org/schemas/domain/qemu/1.0')
            qemu_cap = ET.Element("qemu:capabilities")
            elem.insert(-1, qemu_cap)
            incbackup_cap = ET.Element("qemu:add")
            incbackup_cap.set('capability', 'incremental-backup')
            qemu_cap.insert(1, incbackup_cap)
        vmxml.undefine()
        tmp_vm_xml = os.path.join(tmp_dir, "tmp_vm.xml")
        tree.write(tmp_vm_xml)
        virsh.define(tmp_vm_xml)
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        logging.debug("Script insert xml elements to make sure vm can support "
                      "incremental backup. This should be removded when "
                      "bz 1799015 fixed.")

        # Prepare the disk to be backuped.
        disk_params = {}
        disk_path = ""
        if original_disk_type == "local":
            image_name = "{}_image.qcow2".format(original_disk_target)
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
        elif original_disk_type == "ceph":
            ceph_mon_host = params.get("ceph_mon_host", "EXAMPLE_MON_HOST_AUTHX")
            ceph_host_port = params.get("ceph_host_port", "EXAMPLE_PORT")
            ceph_pool_name = params.get("ceph_pool_name", "EXAMPLE_POOL")
            ceph_file_name = params.get("ceph_file_name", "EXAMPLE_FILE")
            ceph_disk_name = ceph_pool_name + "/" + ceph_file_name
            ceph_client_name = params.get("ceph_client_name", "EXAMPLE_CLIENT_NAME")
            ceph_client_key = params.get("ceph_client_key", "EXAMPLE_CLIENT_KEY")
            ceph_auth_user = params.get("ceph_auth_user", "EXAMPLE_AUTH_USER")
            ceph_auth_key = params.get("ceph_auth_key", "EXAMPLE_AUTH_KEY")
            auth_sec_usage_type = "ceph"

            enable_auth = "yes" == params.get("enable_auth", "yes")
            key_file = os.path.join(tmp_dir, "ceph.key")
            key_opt = ""
            # Prepare a blank params to confirm if delete the configure at the end of the test
            ceph_cfg = ""
            if not utils_package.package_install(["ceph-common"]):
                test.error("Failed to install ceph-common")
            # Create config file if it doesn't exist
            ceph_cfg = ceph.create_config_file(ceph_mon_host)
            if enable_auth:
                # If enable auth, prepare a local file to save key
                if ceph_client_name and ceph_client_key:
                    with open(key_file, 'w') as f:
                        f.write("[%s]\n\tkey = %s\n" %
                                (ceph_client_name, ceph_client_key))
                    key_opt = "--keyring %s" % key_file
                    auth_sec_dict = {"sec_usage": auth_sec_usage_type,
                                     "sec_name": "ceph_auth_secret"}
                    auth_sec_uuid = libvirt.create_secret(auth_sec_dict)
                    virsh.secret_set_value(auth_sec_uuid, ceph_auth_key,
                                           debug=True)
                    disk_params_auth = {"auth_user": ceph_auth_user,
                                        "secret_type": auth_sec_usage_type,
                                        "secret_uuid": auth_sec_uuid,
                                        "auth_in_source": True}
                else:
                    test.error("No ceph client name/key provided.")
                disk_path = "rbd:%s:mon_host=%s:keyring=%s" % (ceph_disk_name,
                                                               ceph_mon_host,
                                                               key_file)
            ceph.rbd_image_rm(ceph_mon_host, ceph_pool_name,
                              ceph_file_name, ceph_cfg, key_file)
            process.run("qemu-img create -f qcow2 %s %s" % (disk_path, original_disk_size),
                        shell=True, verbose=True)
            disk_params = {'device_type': 'disk',
                           'type_name': 'network',
                           "driver_type": "qcow2",
                           'target_dev': original_disk_target}
            disk_params_src = {'source_protocol': 'rbd',
                               'source_name': ceph_disk_name,
                               'source_host_name': ceph_mon_host,
                               'source_host_port': ceph_host_port}
            disk_params.update(disk_params_src)
            disk_params.update(disk_params_auth)
        else:
            test.error("The disk type '%s' not supported in this script." %
                       original_disk_type)
        if hotplug_disk:
            vm.start()
            session = vm.wait_for_login().close()
            disk_xml = libvirt.create_disk_xml(disk_params)
            virsh.attach_device(vm_name, disk_xml, debug=True)
        else:
            disk_xml = libvirt.create_disk_xml(disk_params)
            virsh.attach_device(vm.name, disk_xml,
                                flagstr="--config", debug=True)
            vm.start()
        session = vm.wait_for_login()
        new_disks_in_vm = list(utils_disk.get_linux_disks(session).keys())
        session.close()
        if len(new_disks_in_vm) != 1:
            test.fail("Test disk not prepared in vm")

        # Use the newly added disk as test disk
        test_disk_in_vm = "/dev/" + new_disks_in_vm[0]
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_disks = list(vmxml.get_disk_all().keys())

        checkpoint_list = []
        is_incremental = False
        backup_path_list = []
        for backup_index in range(backup_rounds):
            # Prepare backup xml
            backup_params = {"backup_mode": "push"}
            if backup_index > 0:
                is_incremental = True
                backup_params["backup_incremental"] = "checkpoint_" + str(backup_index - 1)

            backup_disk_xmls = []
            for vm_disk in vm_disks:
                backup_disk_params = {"disk_name": vm_disk}
                if vm_disk != original_disk_target:
                    backup_disk_params["enable_backup"] = "no"
                else:
                    backup_disk_params["enable_backup"] = "yes"
                    backup_disk_params["disk_type"] = target_type
                    target_params = {"attrs": {}}
                    if target_type == "file":
                        target_file_name = "target_file_%s" % backup_index
                        target_file_path = os.path.join(tmp_dir, target_file_name)
                        if prepare_target_file:
                            libvirt.create_local_disk("file", target_file_path,
                                                      original_disk_size, target_driver)
                        target_params["attrs"]["file"] = target_file_path
                        backup_path_list.append(target_file_path)
                    elif target_type == "block":
                        if prepare_target_blkdev:
                            target_blkdev_path = libvirt.setup_or_cleanup_iscsi(
                                    is_setup=True, image_size=target_blkdev_size)
                        target_params["attrs"]["dev"] = target_blkdev_path
                        backup_path_list.append(target_blkdev_path)
                    else:
                        test.fail("We do not support backup target type: '%s'"
                                  % target_type)
                    logging.debug("target params: %s", target_params)
                    backup_disk_params["backup_target"] = target_params
                    driver_params = {"type": target_driver}
                    backup_disk_params["backup_driver"] = driver_params
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

            if reuse_target_file:
                backup_options += " --reuse-external"
            backup_result = virsh.backup_begin(vm_name, backup_options,
                                               debug=True)
            if backup_result.exit_status:
                raise utils_backup.BackupBeginError(backup_result.stderr.strip())

            # Wait for the backup job actually finished
            if not utils_misc.wait_for(
                    lambda: backup_job_done(vm_name, original_disk_target), 60):
                test.fail("Backup job not finished in 60s")

        for checkpoint_name in checkpoint_list:
            virsh.checkpoint_delete(vm_name, checkpoint_name, debug=True)
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Compare the backup data and original data
        original_data_file = os.path.join(tmp_dir, "original_data.qcow2")
        cmd = "qemu-img convert -f qcow2 %s -O qcow2 %s" % (disk_path, original_data_file)
        process.run(cmd, shell=True, verbose=True)

        for backup_path in backup_path_list:
            if target_driver == "qcow2":
                # Clear backup image's backing file before comparison
                qemu_cmd = ("qemu-img rebase -u -f qcow2 -b '' -F qcow2 %s"
                            % backup_path)
                process.run(qemu_cmd, shell=True, verbose=True)
            if not utils_backup.cmp_backup_data(original_data_file, backup_path,
                                                backup_file_driver=target_driver):
                test.fail("Backup and original data are not identical for"
                          "'%s' and '%s'" % (disk_path, backup_path))
            else:
                logging.debug("'%s' contains correct backup data", backup_path)
    except utils_backup.BackupBeginError as details:
        if backup_error:
            logging.debug("Backup failed as expected.")
        else:
            test.fail(details)
    finally:
        # Remove checkpoints
        if "checkpoint_list" in locals() and checkpoint_list:
            for checkpoint_name in checkpoint_list:
                virsh.checkpoint_delete(vm_name, checkpoint_name)

        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Restoring vm
        vmxml_backup.sync()

        # Remove iscsi devices
        libvirt.setup_or_cleanup_iscsi(False)

        # Remove ceph related data
        if original_disk_type == "ceph":
            ceph.rbd_image_rm(ceph_mon_host, ceph_pool_name,
                              ceph_file_name, ceph_cfg, key_file)
            if "auth_sec_uuid" in locals() and auth_sec_uuid:
                virsh.secret_undefine(auth_sec_uuid)
            if "ceph_cfg" in locals() and os.path.exists(ceph_cfg):
                os.remove(ceph_cfg)
            if os.path.exists(key_file):
                os.remove(key_file)
