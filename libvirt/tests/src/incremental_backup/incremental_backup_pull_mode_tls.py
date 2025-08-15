import os
from virttest import data_dir
from virttest import libvirt_version
from virttest import virsh
from virttest import utils_disk
from virttest import utils_backup
from virttest import utils_libvirtd
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import backup_xml
from virttest.utils_config import LibvirtQemuConfig
from virttest.utils_conn import TLSConnection
from virttest.utils_libvirt import libvirt_secret
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.virtual_disk import disk_base


def prepare_tls_env(test, params, qemu_config):
    """
    Prepare the environment for tls backup test.

    :params return: return the tuple of tls object and qemu config.
    """
    local_ip = params.get("local_ip")
    local_hostname = params.get("local_hostname")
    local_user_name = params.get("local_user_name")
    local_user_password = params.get("local_user_password")
    custom_pki_path = params.get("pki_path")
    tls_provide_client_cert = "yes" == params.get("tls_provide_client_cert", "no")
    tls_config = {"qemu_tls": "yes",
                  "server_ip": local_ip,
                  "server_user": local_user_name,
                  "server_pwd": local_user_password,
                  "server_cn": local_hostname,
                  "client_cn": local_hostname,
                  "client_ip": local_ip,
                  "client_pwd": local_user_password,
                  "client_user": local_user_name,
                  "custom_pki_path": custom_pki_path,
                  }
    tls_obj = TLSConnection(tls_config)
    tls_obj.auto_recover = True
    tls_obj.conn_setup(True, tls_provide_client_cert)
    test.log.debug("TLS certs in: %s" % custom_pki_path)
    qemu_config.backup_tls_x509_verify = True
    utils_libvirtd.Libvirtd("virtqemud").restart()
    return tls_obj, qemu_config


def run(test, params, env):
    """
    Test the pull-mode backup function when tls is enabled.
    """
    def prepare_guest():
        """
        Prepare the guest for tls backup test.
        """
        disk_obj.new_image_path = data_dir.get_data_dir() + '/test.qcow2'
        libvirt.create_local_disk(
                    "file", path=disk_obj.new_image_path, size=target_disk_size,
                    disk_format="qcow2")
        disk_obj.add_vm_disk(disk_type, disk_dict, disk_obj.new_image_path)
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login().close()
        test.log.debug(
            "The current guest xml is %s", virsh.dumpxml(vm_name).stdout_text
        )

    def prepare_backup_xml():
        """
        Prepare the backup xml.

        :params return: return the backup options.
        """
        backup_dev = backup_xml.BackupXML()
        backup_dev.setup_attrs(**backup_dict)
        test.log.debug("The backup xml is %s", backup_dev)
        backup_options = backup_dev.xml
        return backup_options

    def backup_begin(backup_options):
        """
        Begin the backup process

        :param backup_options: The xml options for the backup
        """
        backup_result = virsh.backup_begin(
            vm_name, backup_options, debug=True, ignore_status=False
        )
        if backup_result.exit_status:
            raise utils_backup.BackupBeginError(backup_result.stderr.strip())

    def create_libvirt_secret():
        """
        Create the libvirt secret for tls authentication

        :params return: return the secret uuid.
        """
        secret_passphrase = params.get("secret_passphrase")
        secret_dict = eval(params.get("secret_dict"))
        libvirt_secret.clean_up_secrets()
        secret_uuid = libvirt_secret.create_secret(secret_dict)
        qemu_config.backup_tls_x509_secret_uuid = secret_uuid
        utils_libvirtd.Libvirtd("virtqemud").restart()
        virsh.secret_set_value(secret_uuid, secret_passphrase, encode=True, debug=True)
        return secret_uuid

    def write_datas():
        """
        Write datas to the disk in guest.
        """
        dd_seek = params.get("dd_seek")
        dd_count = params.get("dd_count")
        vm_session = vm.wait_for_login()
        new_disk = libvirt_disk.get_non_root_disk_name(vm_session)[0]
        utils_disk.dd_data_to_vm_disk(vm_session, "/dev/%s" % new_disk, seek=dd_seek, count=dd_count)
        vm_session.close()

    def pull_full_backup_to_file():
        """
        Pull full backup to file.

        :params return: return the path of the backup file.
        """
        nbd_config = {"nbd_protocol": params.get("nbd_protocol"),
                      "nbd_hostname": local_hostname,
                      "nbd_tcp_port": "10809",
                      "nbd_export": target_disk,
                      "tls_dir": custom_pki_path
                      }
        backup_file_path = os.path.join(
                    data_dir.get_data_dir(), "backup_file.qcow2")
        try:
            utils_backup.pull_full_backup_to_file(nbd_config, backup_file_path)
        except Exception as details:
            test.fail("Fail to get full backup data: %s" % details)
        test.log.debug("Full backup to: %s", backup_file_path)
        return backup_file_path

    libvirt_version.is_libvirt_feature_supported(params)
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    disk_type = params.get("disk_type")
    target_disk = params.get("target_disk", "vdb")
    target_disk_size = params.get("target_disk_size")
    disk_dict = eval(params.get("disk_dict"))
    backup_dict = eval(params.get("backup_dict"))
    restart_virtqemud = "yes" == params.get("restart_virtqemud")
    with_secret_uuid = "yes" == params.get("with_secret_uuid")
    local_hostname = params.get("local_hostname")
    custom_pki_path = params.get("pki_path")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    qemu_config = LibvirtQemuConfig()
    disk_obj = disk_base.DiskBase(test, vm, params)
    tls_obj = None

    try:
        test.log.info("SETUP_TEST:prepare TLS environment.")
        tls_obj = prepare_tls_env(test, params, qemu_config)
        if with_secret_uuid:
            test.log.info("SETUP_TEST: create the secret.")
            secret_uuid = create_libvirt_secret()
        test.log.info("SETUP_TEST: prepare the guest.")
        prepare_guest()
        if with_secret_uuid:
            write_datas()
        test.log.info("TEST_STEP: start the backup.")
        backup_options = prepare_backup_xml()
        backup_begin(backup_options)
        if restart_virtqemud:
            test.log.info("TEST_STEP: restart the virtqemud and check the job status.")
            utils_libvirtd.Libvirtd("virtqemud").restart()
            job_info = virsh.domjobinfo(vm_name).stdout_text
            if "Backup" not in job_info:
                test.fail("The backup job is not running!")
        if with_secret_uuid:
            test.log.info("TEST_STEP: dump the backup data to a local image.")
            backup_file_path = pull_full_backup_to_file()
            image_info = utils_misc.get_image_info(backup_file_path)
            if image_info['dsize'] >= 100:
                test.log.info("The backup file size is correct.")
            else:
                test.fail("The backup file size is not correct!")
        virsh.domjobabort(vm_name, debug=True, ignore_status=False)
        if restart_virtqemud:
            test.log.info("TEST_step: start the backup again after restarting virtqemud.")
            backup_begin(backup_options)
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        qemu_config.restore()
        disk_obj.cleanup_disk_preparation(disk_type)
        if tls_obj:
            del tls_obj
        if with_secret_uuid:
            virsh.secret_undefine(secret_uuid, ignore_status=True)
            if backup_file_path:
                os.remove(backup_file_path)
