import os
import logging
import time

from avocado.utils import process

from virttest import libvirt_vm
from virttest import defaults
from virttest import virsh
from virttest import migration
from virttest import remote
from virttest import utils_disk
from virttest import utils_conn
from virttest import utils_misc
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_pcicontr


def run(test, params, env):
    """
    Test migration over unix socket.
    1) Migrate vm over unix socket
    2) Migrate vm over unix socket - libvirt tunnelled(--tunnelled)
    3) Migrate vm over unix socket - enable multifd(--parallel)
    4) Migrate vm with copy storage over unix socket - one disk
    5) Migrate vm with copy storage over unix socket - multiple disks
    6) Abort migration over unix socket and migrate again
    7) Abort migration with copy storage over unix socket, and migrate again
    8) Migrate vm with copy storage over unix socket - multiple disks
        - enable multifd(--parallel)

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def update_disk(vm, params):
        """
        Update disk for testing.

        :param vm: vm object.
        :param params: the parameters used.
        :return: updated images.
        """
        local_image_list = []
        remote_image_list = []
        vm_did_list = []
        # Change the disk of the vm
        if storage_type == "nfs":
            libvirt.set_vm_disk(vm, params)
        else:
            disk_format = params.get("disk_format", "qcow2")
            disk_num = eval(params.get("disk_num", "1"))
            blk_source = vm.get_first_disk_devices()['source']
            vsize = utils_misc.get_image_info(blk_source).get("vsize")
            remote_session = remote.remote_login("ssh", server_ip, "22",
                                                 server_user, server_pwd,
                                                 r'[$#%]')
            # Create disk on remote host
            utils_misc.make_dirs(os.path.dirname(blk_source), remote_session)
            libvirt_disk.create_disk("file", disk_format=disk_format,
                                     path=blk_source, size=vsize,
                                     session=remote_session)
            remote_image_list.append(blk_source)

            for idx in range(2, disk_num+1):
                disk_path = os.path.join(os.path.dirname(blk_source),
                                         "test%s.img" % str(idx))
                # Create disk on local
                libvirt_disk.create_disk("file", disk_format=disk_format,
                                         path=disk_path)
                local_image_list.append(disk_path)

                target_dev = 'vd' + chr(idx + ord('a') - 1)
                new_disk_dict = {"driver_type": disk_format}
                vm_was_running = vm.is_alive()
                libvirt_pcicontr.reset_pci_num(vm_name)
                if vm_was_running and not vm.is_alive():
                    vm.start()
                    vm.wait_for_login().close()
                result = libvirt.attach_additional_device(vm_name, target_dev,
                                                          disk_path,
                                                          new_disk_dict, False)
                libvirt.check_exit_status(result)

                libvirt_disk.create_disk("file", disk_format=disk_format,
                                         path=disk_path,
                                         session=remote_session)

                remote_image_list.append(disk_path)
                vm_did_list.append(target_dev)

            remote_session.close()
        return local_image_list, remote_image_list, vm_did_list

    def check_socket(params):
        """
        Check sockets' number

        :param params: the parameters used
        :raise: test.fail when command fails
        """
        postcopy_options = params.get("postcopy_options")
        vm_name = params.get("migrate_main_vm")
        exp_num = params.get("expected_socket_num", "2")
        if postcopy_options:
            migration_test.set_migratepostcopy(vm_name)
        cmd = ("netstat -xanp|grep -E \"CONNECTED"
               ".*(desturi-socket|migrateuri-socket)\" | wc -l")
        res = process.run(cmd, shell=True).stdout_text.strip()
        if res != exp_num:
            test.fail("There should be {} connected unix sockets, "
                      "but found {} sockets.".format(exp_num, res))

    migration_test = migration.MigrationTest()
    migration_test.check_parameters(params)

    # Local variables
    virsh_args = {"debug": True}
    server_ip = params["server_ip"] = params.get("remote_ip")
    server_user = params["server_user"] = params.get("remote_user", "root")
    server_pwd = params["server_pwd"] = params.get("remote_pwd")
    client_ip = params["client_ip"] = params.get("local_ip")
    client_pwd = params["client_pwd"] = params.get("local_pwd")
    virsh_options = params.get("virsh_options", "")
    extra = params.get("virsh_migrate_extra")
    options = params.get("virsh_migrate_options", "--live --p2p --verbose")
    storage_type = params.get("storage_type")
    disk_num = params.get("disk_num")
    desturi_port = params.get("desturi_port", "22222")
    migrateuri_port = params.get("migrateuri_port", "33333")
    disks_uri_port = params.get("disks_uri_port", "44444")
    migrate_again = "yes" == params.get("migrate_again", "no")
    action_during_mig = params.get("action_during_mig")
    if action_during_mig:
        action_during_mig = eval(action_during_mig)

    extra_args = migration_test.update_virsh_migrate_extra_args(params)

    mig_result = None
    local_image_list = []
    remote_image_list = []
    vm_did_list = []

    if not libvirt_version.version_compare(6, 6, 0):
        test.cancel("This libvirt version doesn't support "
                    "migration over unix.")

    if storage_type == "nfs":
        # Params for NFS shared storage
        shared_storage = params.get("migrate_shared_storage", "")
        if shared_storage == "":
            default_guest_asset = defaults.get_default_guest_os_info()['asset']
            default_guest_asset = "%s.qcow2" % default_guest_asset
            shared_storage = os.path.join(params.get("nfs_mount_dir"),
                                          default_guest_asset)
            logging.debug("shared_storage:%s", shared_storage)

        # Params to update disk using shared storage
        params["disk_type"] = "file"
        params["disk_source_protocol"] = "netfs"
        params["mnt_path_name"] = params.get("nfs_mount_dir")

    # params for migration connection
    params["virsh_migrate_connect_uri"] = libvirt_vm.complete_uri(
        params.get("migrate_source_host"))
    src_uri = params.get("virsh_migrate_connect_uri")
    dest_uri = params.get("virsh_migrate_desturi")
    dest_uri_ssh = libvirt_vm.complete_uri(params.get("migrate_dest_host"))

    unix_obj = None

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    vm.verify_alive()
    bk_uri = vm.connect_uri

    postcopy_options = params.get("postcopy_options")
    if postcopy_options:
        extra = "%s %s" % (extra, postcopy_options)

    # For safety reasons, we'd better back up  xmlfile.
    new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = new_xml.copy()

    try:
        unix_obj = utils_conn.UNIXSocketConnection(params)
        unix_obj.conn_setup()
        unix_obj.auto_recover = True

        local_image_list, remote_image_list, vm_did_list = update_disk(vm, params)

        if not vm.is_alive():
            vm.start()

        logging.debug("Guest xml after starting:\n%s",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))

        vm_session = vm.wait_for_login()

        for did in vm_did_list:
            utils_disk.linux_disk_check(vm_session, did)

        # Execute migration process
        vms = [vm]

        migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                    options, thread_timeout=600,
                                    ignore_status=True, virsh_opt=virsh_options,
                                    func=action_during_mig,
                                    extra_opts=extra,
                                    **extra_args)

        mig_result = migration_test.ret

        if migrate_again:
            logging.debug("Sleeping 10 seconds before rerunning the migration.")
            time.sleep(10)
            if params.get("migrate_again_clear_func", "yes") == "yes":
                action_during_mig = None
            extra_args["status_error"] = params.get(
                "migrate_again_status_error", "no")
            migration_test.do_migration(vms, None, dest_uri, 'orderly',
                                        options, thread_timeout=900,
                                        ignore_status=True,
                                        virsh_opt=virsh_options,
                                        extra_opts=extra,
                                        func=action_during_mig,
                                        **extra_args)

            mig_result = migration_test.ret

        if int(mig_result.exit_status) == 0:
            vm.connect_uri = dest_uri_ssh
            if not utils_misc.wait_for(
               lambda: virsh.is_alive(vm_name, uri=dest_uri_ssh,
                                      debug=True), 60):
                test.fail("The migrated VM should be alive!")
            if vm_did_list:
                vm_session_after_mig = vm.wait_for_serial_login(timeout=240)
                for did in vm_did_list:
                    vm_session_after_mig.cmd(
                        "echo mytest >> /mnt/%s1/mytest" % did)
    finally:
        logging.info("Recover test environment")
        vm.connect_uri = bk_uri
        # Clean VM on destination and source
        migration_test.cleanup_vm(vm, dest_uri)

        orig_config_xml.sync()

        # Remove image files
        for source_file in local_image_list:
            libvirt.delete_local_disk("file", path=source_file)
        for img in remote_image_list:
            remote.run_remote_cmd("rm -rf %s" % img, params)
