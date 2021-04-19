import os
import logging
import time

from avocado.utils import process

from virttest import libvirt_version
from virttest import libvirt_vm
from virttest import utils_misc
from virttest import migration
from virttest import remote

from virttest.utils_conn import TLSConnection
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_config
from virttest.utils_libvirt import libvirt_network


def run(test, params, env):
    """
    Test storage migration
    1) Do storage migration(copy-storage-all/copy-storage-inc) with
    TLS encryption - NBD transport
    2) Cancel storage migration with TLS encryption
    3) Copy only the top image for storage migration with backing chain
    4) Migrate vm with copy storage - Native TLS(--tls) - inconsistent CN and
        server hostname
    5) Migrate vm with copy storage over TCP transport - Specified IP
    6) Migrate vm with copy storage over TCP transport - Specified IP+Port
    7) Migrate vm with copy storage over TCP transport - Specified disks_uri

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def prepare_nfs_backingfile(vm, params):
        """
        Create an image using nfs type backing_file

        :param vm: The guest
        :param params: the parameters used
        """
        mnt_path_name = params.get("nfs_mount_dir", "nfs-mount")
        exp_opt = params.get("export_options", "rw,no_root_squash,fsid=0")
        exp_dir = params.get("export_dir", "nfs-export")
        backingfile_img = params.get("source_dist_img", "nfs-img")
        disk_format = params.get("disk_format", "qcow2")
        img_name = params.get("img_name", "test.img")
        precreation = "yes" == params.get("precreation", "yes")
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
        disk_xml = vmxml.devices.by_device_tag('disk')[0]
        src_disk_format = disk_xml.xmltreefile.find('driver').get('type')
        first_disk = vm.get_first_disk_devices()
        blk_source = first_disk['source']
        disk_img = os.path.join(os.path.dirname(blk_source), img_name)

        res = libvirt.setup_or_cleanup_nfs(True, mnt_path_name, is_mount=True,
                                           export_options=exp_opt,
                                           export_dir=exp_dir)
        mnt_path = res["mount_dir"]
        params["selinux_status_bak"] = res["selinux_status_bak"]

        if vm.is_alive():
            vm.destroy(gracefully=False)

        disk_cmd = ("qemu-img convert -f %s -O %s %s %s/%s" %
                    (src_disk_format, disk_format,
                     blk_source, mnt_path, backingfile_img))
        process.run(disk_cmd, ignore_status=False, verbose=True)
        local_image_list.append("%s/%s" % (mnt_path, backingfile_img))
        logging.debug("Create a local image backing on NFS.")
        disk_cmd = ("qemu-img create -f %s -b %s/%s %s" %
                    (disk_format, mnt_path, backingfile_img, disk_img))
        process.run(disk_cmd, ignore_status=False, verbose=True)
        local_image_list.append(disk_img)
        if precreation:
            logging.debug("Create an image backing on NFS on remote host.")
            remote_session = remote.remote_login("ssh", server_ip, "22",
                                                 server_user, server_pwd,
                                                 r'[$#%]')
            utils_misc.make_dirs(os.path.dirname(blk_source), remote_session)
            status, stdout = utils_misc.cmd_status_output(
                disk_cmd, session=remote_session)
            logging.debug("status: {}, stdout: {}".format(status, stdout))
            remote_image_list.append("%s/%s" % (mnt_path, backingfile_img))
            remote_image_list.append(disk_img)
            remote_session.close()

        params.update({'disk_source_name': disk_img,
                       'disk_type': 'file',
                       'disk_source_protocol': 'file'})
        libvirt.set_vm_disk(vm, params)

    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)

    # Local variables
    server_ip = params["server_ip"] = params.get("remote_ip")
    server_user = params["server_user"] = params.get("remote_user", "root")
    server_pwd = params["server_pwd"] = params.get("remote_pwd")
    client_ip = params["client_ip"] = params.get("local_ip")
    client_pwd = params["client_pwd"] = params.get("local_pwd")
    virsh_options = params.get("virsh_options", "")
    copy_storage_option = params.get("copy_storage_option")
    extra = params.get("virsh_migrate_extra", "")
    options = params.get("virsh_migrate_options", "--live --verbose")
    backingfile_type = params.get("backingfile_type")
    check_str_local_log = params.get("check_str_local_log", "")
    disk_format = params.get("disk_format", "qcow2")
    log_file = params.get("log_outputs", "/var/log/libvirt/libvirtd.log")
    daemon_conf_dict = eval(params.get("daemon_conf_dict", '{}'))
    cancel_migration = "yes" == params.get("cancel_migration", "no")
    check_disks_port = "yes" == params.get("check_disks_port", "no")
    migrate_again = "yes" == params.get("migrate_again", "no")
    precreation = "yes" == params.get("precreation", "yes")
    tls_recovery = "yes" == params.get("tls_auto_recovery", "yes")
    status_error = "yes" == params.get("status_error", "no")

    local_image_list = []
    remote_image_list = []
    tls_obj = None

    action_during_mig = None
    daemon_conf = None
    mig_result = None
    remote_session = None
    vm_session = None
    remove_dict = {}
    src_libvirt_file = None

    libvirt_version.is_libvirt_feature_supported(params)

    # params for migration connection
    params["virsh_migrate_desturi"] = libvirt_vm.complete_uri(
                                       params.get("migrate_dest_host"))
    dest_uri = params.get("virsh_migrate_desturi")

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()

    extra = "{} {}".format(extra, copy_storage_option)
    extra_args = migration_test.update_virsh_migrate_extra_args(params)
    if cancel_migration:
        action_during_mig = migration_test.do_cancel
    elif check_disks_port:
        action_during_mig = libvirt_network.check_established

    # For safety reasons, we'd better back up  xmlfile.
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        if backingfile_type:
            if backingfile_type == "nfs":
                prepare_nfs_backingfile(vm, params)
        if extra.count("copy-storage-all") and precreation:
            blk_source = vm.get_first_disk_devices()['source']
            vsize = utils_misc.get_image_info(blk_source).get("vsize")
            remote_session = remote.remote_login("ssh", server_ip, "22",
                                                 server_user, server_pwd,
                                                 r'[$#%]')
            utils_misc.make_dirs(os.path.dirname(blk_source), remote_session)
            disk_cmd = ("qemu-img create -f %s %s %s" %
                        (disk_format, blk_source, vsize))
            status, stdout = utils_misc.cmd_status_output(
                disk_cmd, session=remote_session)
            logging.debug("status: {}, stdout: {}".format(status, stdout))
            remote_image_list.append(blk_source)
            remote_session.close()

        # Update libvirtd configuration
        if daemon_conf_dict:
            if os.path.exists(log_file):
                os.remove(log_file)
            daemon_conf = libvirt.customize_libvirt_config(daemon_conf_dict)

        if extra.count("--tls"):
            tls_obj = TLSConnection(params)
            if tls_recovery:
                tls_obj.auto_recover = True
                tls_obj.conn_setup()

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))
        # Check local guest network connection before migration
        vm_session = vm.wait_for_login(restart_network=True)
        migration_test.ping_vm(vm, params)

        remove_dict = {"do_search": '{"%s": "ssh:/"}' % dest_uri}
        src_libvirt_file = libvirt_config.remove_key_for_modular_daemon(
            remove_dict)

        # Execute migration process
        vms = [vm]

        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                    options, thread_timeout=900,
                                    ignore_status=True, virsh_opt=virsh_options,
                                    extra_opts=extra, func=action_during_mig,
                                    **extra_args)

        mig_result = migration_test.ret

        if migrate_again and status_error:
            logging.debug("Sleeping 10 seconds before rerunning the migration.")
            time.sleep(10)
            if cancel_migration:
                action_during_mig = None
            extra_args["status_error"] = "no"
            migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                        options, thread_timeout=900,
                                        ignore_status=True,
                                        virsh_opt=virsh_options,
                                        extra_opts=extra,
                                        func=action_during_mig,
                                        **extra_args)

            mig_result = migration_test.ret
        if int(mig_result.exit_status) == 0:
            migration_test.ping_vm(vm, params, uri=dest_uri)

        if check_str_local_log:
            libvirt.check_logfile(check_str_local_log, log_file)

    finally:
        logging.debug("Recover test environment")
        # Clean VM on destination and source
        migration_test.cleanup_vm(vm, dest_uri)
        orig_config_xml.sync()

        if daemon_conf:
            logging.debug("Recover the configurations")
            libvirt.customize_libvirt_config(None, is_recover=True,
                                             config_object=daemon_conf)
        if src_libvirt_file:
            src_libvirt_file.restore()

        if tls_obj:
            logging.debug("Clean up local objs")
            del tls_obj
        for source_file in local_image_list:
            libvirt.delete_local_disk("file", path=source_file)
        for img in remote_image_list:
            remote.run_remote_cmd("rm -rf %s" % img, params)

        if remote_session:
            remote_session.close()
