import os
import logging
from autotest.client import lv_utils
from autotest.client.shared import error, ssh_key
from virttest import utils_test, remote, libvirt_vm
from virttest.utils_test import libvirt as utlv


def copied_migration(vms, params):
    """
    Migrate vms with storage copied.
    """
    dest_uri = params.get("migrate_dest_uri")
    remote_host = params.get("migrate_dest_host")
    copy_option = params.get("copy_storage_option", "")
    username = params.get("migrate_dest_user", "root")
    password = params.get("migrate_dest_pwd")
    timeout = int(params.get("thread_timeout", 1200))
    options = "--live %s" % copy_option

    # Get vm ip for remote checking
    vms_ip = {}
    for vm in vms:
        if vm.is_dead():
            vm.start()
        vm.wait_for_login()
        vms_ip[vm.name] = vm.get_address()

    cp_mig = utlv.MigrationTest()
    cp_mig.do_migration(vms, None, dest_uri, "orderly", options, timeout)
    check_ip_failures = []
    if cp_mig.RET_MIGRATION:
        for vm in vms:
            try:
                utils_test.check_dest_vm_network(vm, vms_ip[vm.name],
                                                 remote_host, username,
                                                 password)
            except error.TestFail, detail:
                check_ip_failures.append(str(detail))
            cp_mig.cleanup_dest_vm(vm, None, dest_uri)
    else:
        for vm in vms:
            cp_mig.cleanup_dest_vm(vm, None, dest_uri)
        raise error.TestFail("Migrate vms with storage copied failed.")

    if len(check_ip_failures):
        raise error.TestFail("Check IP failed:%s", check_ip_failures)


def run(test, params, env):
    """
    Test migration with option --copy-storage-all or --copy-storage-inc.
    """
    vm = env.get_vm(params.get("main_vm"))
    disk_type = params.get("copy_storage_type", "file")
    if disk_type == "file":
        params['added_disk_type'] = "file"
    else:
        params['added_disk_type'] = "block"
    primary_target = vm.get_first_disk_devices()["target"]
    file_path, file_size = vm.get_device_size(primary_target)
    # Convert to Gib
    file_size = int(file_size) / 1073741824

    remote_host = params.get("migrate_dest_host", "REMOTE.EXAMPLE")
    local_host = params.get("migrate_source_host", "LOCAL.EXAMPLE")
    remote_user = params.get("migrate_dest_user", "root")
    remote_passwd = params.get("migrate_dest_pwd")
    if remote_host.count("EXAMPLE") or local_host.count("EXAMPLE"):
        raise error.TestNAError("Config remote or local host first.")
    # Config ssh autologin for it
    ssh_key.setup_ssh_key(remote_host, remote_user, remote_passwd, port=22)

    # Attach additional disks to vm if disk count big than 1
    disks_count = int(params.get("added_disks_count", 1)) - 1
    if disks_count:
        new_vm_name = "%s_smtest" % vm.name
        if vm.is_alive():
            vm.destroy()
        utlv.define_new_vm(vm.name, new_vm_name)
        vm = libvirt_vm.VM(new_vm_name, vm.params, vm.root_dir,
                           vm.address_cache)
    vms = [vm]
    if vm.is_dead():
        vm.start()

    # Abnormal parameters
    migrate_again = "yes" == params.get("migrate_again", "no")
    abnormal_type = params.get("abnormal_type")

    try:
        rdm = utils_test.RemoteDiskManager(params)
        vgname = params.get("sm_vg_name", "SMTEST")
        added_disks_list = []
        if disk_type == "lvm":
            target1 = target2 = ""  # For cleanup
            # Create volume group with iscsi
            # For local, target is a device name
            target1 = utlv.setup_or_cleanup_iscsi(is_setup=True, is_login=True,
                                                  emulated_image="emulated_iscsi1")
            lv_utils.vg_create(vgname, target1)
            logging.debug("Created VG %s", vgname)
            # For remote, target is real target name
            target2 = utlv.setup_or_cleanup_iscsi(is_setup=True, is_login=False,
                                                  emulated_image="emulated_iscsi2")
            logging.debug("Created target: %s", target2)
            # Login on remote host
            remote_device = rdm.iscsi_login_setup(local_host, target2)
            if not rdm.create_vg(vgname, remote_device):
                raise error.TestError("Create VG %s on %s failed."
                                      % (vgname, remote_host))

        all_disks = utlv.attach_disks(vm, file_path, vgname, params)
        # Reserve for cleanup
        added_disks_list = all_disks.keys()
        all_disks[file_path] = file_size
        logging.debug("All disks need to be migrated:%s", all_disks)

        if abnormal_type == "occupied_disk":
            occupied_path = rdm.occupy_space(disk_type, file_size,
                                             file_path, vgname, timeout=600)
        if not abnormal_type == "not_exist_file":
            for disk, size in all_disks.items():
                if disk == file_path:
                    rdm.create_image("file", disk, size, None, None)
                else:
                    rdm.create_image(disk_type, disk, size, vgname,
                                     os.path.basename(disk))

        fail_flag = False
        try:
            logging.debug("Start migration...")
            copied_migration(vms, params)
            if migrate_again:
                fail_flag = True
                raise error.TestFail("Migration succeed, but not expected!")
            else:
                return
        except error.TestFail:
            if not migrate_again:
                raise

            if abnormal_type == "occupied_disk":
                rdm.remove_path(disk_type, occupied_path)
            elif abnormal_type == "not_exist_file":
                for disk, size in all_disks.items():
                    if disk == file_path:
                        rdm.create_image("file", disk, size, None, None)
                    else:
                        rdm.create_image(disk_type, disk, size, vgname,
                                         os.path.basename(disk))
            elif abnormal_type == "migration_interupted":
                params["thread_timeout"] = 120
            # Raise after cleanup
            if fail_flag:
                raise

            # Migrate it again to confirm failed reason
            copied_migration(vms, params)
    finally:
        # Recover created vm
        if vm.is_alive():
            vm.destroy()
        if disks_count and vm.name == new_vm_name:
            vm.undefine()
        for disk in added_disks_list:
            utlv.delete_local_disk(disk_type, disk)
            rdm.remove_path(disk_type, disk)
        rdm.remove_path("file", file_path)
        if disk_type == "lvm":
            rdm.remove_vg(vgname)
            rdm.iscsi_login_setup(local_host, target2, is_login=False)
            try:
                lv_utils.vg_remove(vgname)
            except:
                pass    # let it go to confirm cleanup iscsi device
            utlv.setup_or_cleanup_iscsi(is_setup=False,
                                        emulated_image="emulated_iscsi1")
            utlv.setup_or_cleanup_iscsi(is_setup=False,
                                        emulated_image="emulated_iscsi2")
