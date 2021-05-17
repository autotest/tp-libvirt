import logging
import os
import re
import signal

from avocado.utils import process

from virttest import data_dir
from virttest import gluster
from virttest import libvirt_version
from virttest import utils_backup
from virttest import utils_disk
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_secret
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_config import LibvirtQemuConfig
from virttest.utils_conn import TLSConnection


def run(test, params, env):
    """
    Test the pull-mode backup function

    Steps:
    1. craete a vm with extra disk vdb
    2. create some data on vdb
    3. start a pull mode full backup on vdb
    4. create some data on vdb
    5. start a pull mode incremental backup
    6. repeat step 5 to 7
    7. check the full/incremental backup file data
    """

    # Basic case config
    hotplug_disk = "yes" == params.get("hotplug_disk", "no")
    original_disk_size = params.get("original_disk_size", "100M")
    original_disk_type = params.get("original_disk_type", "local")
    original_disk_target = params.get("original_disk_target", "vdb")
    local_hostname = params.get("loal_hostname", "localhost")
    local_ip = params.get("local_ip", "127.0.0.1")
    local_user_name = params.get("local_user_name", "root")
    local_user_password = params.get("local_user_password", "redhat")
    tmp_dir = data_dir.get_tmp_dir()
    # Backup config
    scratch_type = params.get("scratch_type", "file")
    reuse_scratch_file = "yes" == params.get("reuse_scratch_file")
    prepare_scratch_file = "yes" == params.get("prepare_scratch_file")
    scratch_blkdev_path = params.get("scratch_blkdev_path")
    scratch_blkdev_size = params.get("scratch_blkdev_size", original_disk_size)
    prepare_scratch_blkdev = "yes" == params.get("prepare_scratch_blkdev")
    backup_rounds = int(params.get("backup_rounds", 3))
    backup_error = "yes" == params.get("backup_error")
    expect_backup_canceled = "yes" == params.get("expect_backup_canceled")
    # NBD service config
    nbd_protocol = params.get("nbd_protocol", "unix")
    nbd_socket = params.get("nbd_socket", "/tmp/pull_backup.socket")
    nbd_tcp_port = params.get("nbd_tcp_port", "10809")
    nbd_hostname = local_hostname
    set_exportname = "yes" == params.get("set_exportname")
    set_exportbitmap = "yes" == params.get("set_exportbitmap")
    # TLS service config
    tls_enabled = "yes" == params.get("tls_enabled")
    tls_x509_verify = "yes" == params.get("tls_x509_verify")
    custom_pki_path = "yes" == params.get("custom_pki_path")
    tls_client_ip = tls_server_ip = local_ip
    tls_client_cn = tls_server_cn = local_hostname
    tls_client_user = tls_server_user = local_user_name
    tls_client_pwd = tls_server_pwd = local_user_password
    tls_provide_client_cert = "yes" == params.get("tls_provide_client_cert")
    tls_error = "yes" == params.get("tls_error")
    # LUKS config
    scratch_luks_encrypted = "yes" == params.get("scratch_luks_encrypted")
    luks_passphrase = params.get("luks_passphrase", "password")

    # Cancel the test if libvirt support related functions
    if not libvirt_version.version_compare(6, 0, 0):
        test.cancel("Current libvirt version doesn't support "
                    "incremental backup.")
    if tls_enabled and not libvirt_version.version_compare(6, 6, 0):
        test.cancel("Current libvirt version doesn't support pull mode "
                    "backup with tls nbd.")

    try:
        vm_name = params.get("main_vm")
        vm = env.get_vm(vm_name)

        # Make sure there is no checkpoint metadata before test
        utils_backup.clean_checkpoints(vm_name)

        # Backup vm xml
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()
        utils_backup.enable_inc_backup_for_vm(vm)

        # Prepare tls env
        if tls_enabled:
            # Prepare pki
            tls_config = {"qemu_tls": "yes",
                          "auto_recover": "yes",
                          "client_ip": tls_client_ip,
                          "server_ip": tls_server_ip,
                          "client_cn": tls_client_cn,
                          "server_cn": tls_server_cn,
                          "client_user": tls_client_user,
                          "server_user": tls_server_user,
                          "client_pwd": tls_client_pwd,
                          "server_pwd": tls_server_pwd,
                          }
            if custom_pki_path:
                pki_path = os.path.join(tmp_dir, "inc_bkup_pki")
            else:
                pki_path = "/etc/pki/libvirt-backup/"
            if tls_x509_verify:
                tls_config["client_ip"] = tls_client_ip
            tls_config["custom_pki_path"] = pki_path
            tls_obj = TLSConnection(tls_config)
            tls_obj.conn_setup(True, tls_provide_client_cert)
            logging.debug("TLS certs in: %s" % pki_path)
            # Set qemu.conf
            qemu_config = LibvirtQemuConfig()
            if tls_x509_verify:
                qemu_config.backup_tls_x509_verify = True
            else:
                qemu_config.backup_tls_x509_verify = False
            if custom_pki_path:
                qemu_config.backup_tls_x509_cert_dir = pki_path
            utils_libvirtd.Libvirtd().restart()

        # Prepare libvirt secret
        if scratch_luks_encrypted:
            utils_secret.clean_up_secrets()
            luks_secret_uuid = libvirt.create_secret(params)
            virsh.secret_set_value(luks_secret_uuid, luks_passphrase,
                                   encode=True, debug=True)

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
        elif original_disk_type == "iscsi":
            iscsi_host = '127.0.0.1'
            iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                                   is_login=False,
                                                                   image_size=original_disk_size,
                                                                   portal_ip=iscsi_host)
            disk_path = ("iscsi://[%s]/%s/%s" % (iscsi_host, iscsi_target, lun_num))
            process.run("qemu-img create -f qcow2 %s %s" % (disk_path, original_disk_size),
                        shell=True, verbose=True)
            disk_params = {'device_type': 'disk',
                           'type_name': 'network',
                           "driver_type": "qcow2",
                           'target_dev': original_disk_target}
            disk_params_src = {'source_protocol': 'iscsi',
                               'source_name': iscsi_target + "/%s" % lun_num,
                               'source_host_name': iscsi_host,
                               'source_host_port': '3260'}
            disk_params.update(disk_params_src)
        elif original_disk_type == "gluster":
            gluster_vol_name = "gluster_vol"
            gluster_pool_name = "gluster_pool"
            gluster_img_name = "gluster.qcow2"
            gluster_host_ip = gluster.setup_or_cleanup_gluster(
                    is_setup=True,
                    vol_name=gluster_vol_name,
                    pool_name=gluster_pool_name,
                    **params)
            disk_path = 'gluster://%s/%s/%s' % (gluster_host_ip,
                                                gluster_vol_name,
                                                gluster_img_name)
            process.run("qemu-img create -f qcow2 %s %s" % (disk_path, original_disk_size),
                        shell=True, verbose=True)
            disk_params = {'device_type': 'disk',
                           'type_name': 'network',
                           "driver_type": "qcow2",
                           'target_dev': original_disk_target}
            disk_params_src = {'source_protocol': 'gluster',
                               'source_name': gluster_vol_name + "/%s" % gluster_img_name,
                               'source_host_name': gluster_host_ip,
                               'source_host_port': '24007'}
            disk_params.update(disk_params_src)
        else:
            test.error("The disk type '%s' not supported in this script.",
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

        # Use the newly added disk as the test disk
        test_disk_in_vm = "/dev/" + new_disks_in_vm[0]

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_disks = list(vmxml.get_disk_all().keys())

        checkpoint_list = []
        is_incremental = False
        backup_file_list = []
        for backup_index in range(backup_rounds):
            # Prepare backup xml
            backup_params = {"backup_mode": "pull"}
            if backup_index > 0:
                is_incremental = True
                backup_params["backup_incremental"] = "checkpoint_" + str(backup_index - 1)

            # Set libvirt default nbd export name and bitmap name
            nbd_export_name = original_disk_target
            nbd_bitmap_name = "backup-" + original_disk_target

            backup_server_dict = {}
            if nbd_protocol == "unix":
                backup_server_dict["transport"] = "unix"
                backup_server_dict["socket"] = nbd_socket
            else:
                backup_server_dict["name"] = nbd_hostname
                backup_server_dict["port"] = nbd_tcp_port
                if tls_enabled:
                    backup_server_dict["tls"] = "yes"
            backup_params["backup_server"] = backup_server_dict
            backup_disk_xmls = []
            for vm_disk in vm_disks:
                backup_disk_params = {"disk_name": vm_disk}
                if vm_disk != original_disk_target:
                    backup_disk_params["enable_backup"] = "no"
                else:
                    backup_disk_params["enable_backup"] = "yes"
                    backup_disk_params["disk_type"] = scratch_type

                    # Custom nbd export name and bitmap name if required
                    if set_exportname:
                        nbd_export_name = original_disk_target + "_custom_exp"
                        backup_disk_params["exportname"] = nbd_export_name
                    if set_exportbitmap:
                        nbd_bitmap_name = original_disk_target + "_custom_bitmap"
                        backup_disk_params["exportbitmap"] = nbd_bitmap_name

                    # Prepare nbd scratch file/dev params
                    scratch_params = {"attrs": {}}
                    scratch_path = None
                    if scratch_type == "file":
                        scratch_file_name = "scratch_file_%s" % backup_index
                        scratch_path = os.path.join(tmp_dir, scratch_file_name)
                        if prepare_scratch_file:
                            libvirt.create_local_disk("file", scratch_path,
                                                      original_disk_size, "qcow2")
                        scratch_params["attrs"]["file"] = scratch_path
                    elif scratch_type == "block":
                        if prepare_scratch_blkdev:
                            scratch_path = libvirt.setup_or_cleanup_iscsi(
                                    is_setup=True, image_size=scratch_blkdev_size)
                        scratch_params["attrs"]["dev"] = scratch_path
                    else:
                        test.fail("We do not support backup scratch type: '%s'"
                                  % scratch_type)
                    if scratch_luks_encrypted:
                        encryption_dict = {"encryption": "luks",
                                           "secret": {"type": "passphrase",
                                                      "uuid": luks_secret_uuid}}
                        scratch_params["encryption"] = encryption_dict
                    logging.debug("scratch params: %s", scratch_params)
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

            # Create some data in vdb
            dd_count = "1"
            dd_seek = str(backup_index * 10 + 10)
            dd_bs = "1M"
            session = vm.wait_for_login()
            utils_disk.dd_data_to_vm_disk(session, test_disk_in_vm, dd_bs,
                                          dd_seek, dd_count)
            session.close()
            # Start backup
            backup_options = backup_xml.xml + " " + checkpoint_xml.xml
            if reuse_scratch_file:
                backup_options += " --reuse-external"
            backup_result = virsh.backup_begin(vm_name, backup_options,
                                               ignore_status=True, debug=True)
            if backup_result.exit_status:
                raise utils_backup.BackupBeginError(backup_result.stderr.strip())
            # If required, do some error operations during backup job
            error_operation = params.get("error_operation")
            if error_operation:
                if "destroy_vm" in error_operation:
                    vm.destroy(gracefully=False)
                if "kill_qemu" in error_operation:
                    utils_misc.safe_kill(vm.get_pid(), signal.SIGKILL)
                if utils_misc.wait_for(lambda: utils_backup.is_backup_canceled(vm_name),
                                       timeout=5):
                    raise utils_backup.BackupCanceledError()
                elif expect_backup_canceled:
                    test.fail("Backup job should be canceled but not.")
            backup_file_path = os.path.join(
                    tmp_dir, "backup_file_%s.qcow2" % str(backup_index))
            backup_file_list.append(backup_file_path)
            nbd_params = {"nbd_protocol": nbd_protocol,
                          "nbd_export": nbd_export_name}
            if nbd_protocol == "unix":
                nbd_params["nbd_socket"] = nbd_socket
            elif nbd_protocol == "tcp":
                nbd_params["nbd_hostname"] = nbd_hostname
                nbd_params["nbd_tcp_port"] = nbd_tcp_port
                if tls_enabled:
                    nbd_params["tls_dir"] = pki_path
                    nbd_params["tls_server_ip"] = tls_server_ip
            if not is_incremental:
                # Do full backup
                try:
                    utils_backup.pull_full_backup_to_file(nbd_params,
                                                          backup_file_path)
                except Exception as details:
                    if tls_enabled and tls_error:
                        raise utils_backup.BackupTLSError(details)
                    else:
                        test.fail("Fail to get full backup data: %s" % details)
                logging.debug("Full backup to: %s", backup_file_path)
            else:
                # Do incremental backup
                utils_backup.pull_incremental_backup_to_file(
                        nbd_params, backup_file_path, nbd_bitmap_name,
                        original_disk_size)
            # Check if scratch file encrypted
            if scratch_luks_encrypted and scratch_path:
                cmd = "qemu-img info -U %s" % scratch_path
                result = process.run(cmd, shell=True, verbose=True).stdout_text.strip()
                if (not re.search("format.*luks", result, re.IGNORECASE) or
                        not re.search("encrypted.*yes", result, re.IGNORECASE)):
                    test.fail("scratch file/dev is not encrypted by LUKS")
            virsh.domjobabort(vm_name, debug=True)

        for checkpoint_name in checkpoint_list:
            virsh.checkpoint_delete(vm_name, checkpoint_name, debug=True)
        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Compare the backup data and original data
        original_data_file = os.path.join(tmp_dir, "original_data.qcow2")
        cmd = "qemu-img convert -f qcow2 %s -O qcow2 %s" % (disk_path, original_data_file)
        process.run(cmd, shell=True, verbose=True)
        for backup_file in backup_file_list:
            if not utils_backup.cmp_backup_data(original_data_file, backup_file):
                test.fail("Backup and original data are not identical for"
                          "'%s' and '%s'" % (disk_path, backup_file))
            else:
                logging.debug("'%s' contains correct backup data", backup_file)
    except utils_backup.BackupBeginError as detail:
        if backup_error:
            logging.debug("Backup failed as expected.")
        else:
            test.fail("Backup failed to start: %s" % detail)
    except utils_backup.BackupTLSError as detail:
        if tls_error:
            logging.debug("Failed to get backup data as expected.")
        else:
            test.fail("Failed to get tls backup data: %s" % detail)
    except utils_backup.BackupCanceledError as detail:
        if expect_backup_canceled:
            logging.debug("Backup canceled as expected.")
            if not vm.is_alive():
                logging.debug("Check if vm can be started again when backup "
                              "canceled.")
                vm.start()
                vm.wait_for_login().close()
        else:
            test.fail("Backup job canceled: %s" % detail)
    finally:
        # Remove checkpoints
        utils_backup.clean_checkpoints(vm_name,
                                       clean_metadata=not vm.is_alive())

        if vm.is_alive():
            vm.destroy(gracefully=False)

        # Restoring vm
        vmxml_backup.sync()

        # Remove iscsi devices
        if original_disk_type == "iscsi" or scratch_type == "block":
            libvirt.setup_or_cleanup_iscsi(False)

        # Remove gluster devices
        if original_disk_type == "gluster":
            gluster.setup_or_cleanup_gluster(
                    is_setup=False,
                    vol_name=gluster_vol_name,
                    pool_name=gluster_pool_name,
                    **params)

        # Recover qemu.conf
        if "qemu_config" in locals():
            qemu_config.restore()

        # Remove tls object
        if "tls_obj" in locals():
            del tls_obj

        # Remove libvirt secret
        if "luks_secret_uuid" in locals():
            virsh.secret_undefine(luks_secret_uuid, ignore_status=True)
