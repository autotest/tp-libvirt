import os
import logging

from avocado.core import exceptions

from virttest import ssh_key
from virttest import utils_test
from virttest import libvirt_vm
from virttest import migration
from virttest.utils_test import libvirt as utlv
from virttest.staging import lv_utils
from virttest import virsh
from virttest import utils_disk
from virttest import remote

from virttest.utils_misc import is_qemu_capability_supported as qemu_test
from virttest.utils_libvirt import libvirt_config


def create_destroy_pool_on_remote(test, action, params):
    """
    This is to create or destroy a specified pool on remote.

    :param action: "create" or "destory"
    :type str
    :param params: a dict for parameters
    :type dict

    :return: True if successful, otherwise False
    :rtype: Boolean
    """
    remote_ip = params.get("migrate_dest_host")
    remote_user = params.get("migrate_dest_user", "root")
    remote_pwd = params.get("migrate_dest_pwd")
    virsh_dargs = {'remote_ip': remote_ip, 'remote_user': remote_user,
                   'remote_pwd': remote_pwd, 'unprivileged_user': None,
                   'ssh_remote_auth': True}

    new_session = virsh.VirshPersistent(**virsh_dargs)
    pool_name = params.get("precreation_pool_name", "tmp_pool_1")
    timeout = params.get("timeout", 60)
    prompt = params.get("prompt", r"[\#\$]\s*$")

    if action == 'create':
        # Firstly check if the pool already exists
        all_pools = new_session.pool_list(option="--all")
        logging.debug("Pools on remote host:\n%s", all_pools)
        if all_pools.stdout.find(pool_name) >= 0:
            logging.debug("The pool %s already exists and skip "
                          "to create it.", pool_name)
            new_session.close_session()
            return True

        pool_type = params.get("precreation_pool_type", "dir")
        pool_target = params.get("precreation_pool_target")
        cmd = "mkdir -p %s" % pool_target
        session = remote.wait_for_login("ssh", remote_ip, 22,
                                        remote_user, remote_pwd, prompt)
        status, output = session.cmd_status_output(cmd, timeout)
        session.close()
        if status:
            new_session.close_session()
            test.fail("Run '%s' on remote host '%s' failed: %s."
                      % (cmd, remote_ip, output))
        ret = new_session.pool_create_as(pool_name, pool_type, pool_target)
    else:  # suppose it is to destroy
        ret = new_session.pool_destroy(pool_name)

    new_session.close_session()
    return ret


def check_output(test, output_msg, params):
    """
    Check if known messages exist in the given output messages.

    :param output_msg: the given output messages
    :param params: the dictionary including necessary parameters

    :raise TestCancel: raised if the known error is found together
                        with some conditions satisfied
    """
    ERR_MSGDICT = {"Bug 1249587": "error: Operation not supported: " +
                   "pre-creation of storage targets for incremental " +
                   "storage migration is not supported",
                   "ERROR 1": "error: internal error: unable to " +
                   "execute QEMU command 'migrate': " +
                   "this feature or command is not currently supported"}

    for (key, value) in list(ERR_MSGDICT.items()):
        logging.debug("Checking expected errors - %s: %s", key, value)
        if output_msg.find(value) >= 0:
            if (key == "ERROR 1" and
                    params.get("support_precreation") is True):
                logging.debug("The error is not expected: '%s'.", value)
            else:
                logging.info("The known error was found: %s --- %s",
                             key, value)
                test.cancel("Known error: %s --- %s in %s" %
                            (key, value, output_msg))


def copied_migration(test, vms, vms_ip, params):
    """
    Migrate vms with storage copied.

    :param test: The test object
    :param vms: VM list
    :param vms_ip: Dict for VM's IP
    :param params: The parameters for the migration
    :return: MigrationTest object
    :raise: test.fail if anything goes wrong
    """
    dest_uri = params.get("migrate_dest_uri")
    remote_host = params.get("migrate_dest_host")
    copy_option = params.get("copy_storage_option", "")
    username = params.get("migrate_dest_user", "root")
    password = params.get("migrate_dest_pwd")
    timeout = int(params.get("thread_timeout", 1200))
    status_error = params.get("status_error", "no")
    options = "--live %s" % copy_option

    cp_mig = migration.MigrationTest()
    cp_mig.do_migration(vms, None, dest_uri, "orderly", options, timeout,
                        ignore_status=True, status_error=status_error)
    check_ip_failures = []

    if cp_mig.RET_MIGRATION:
        for vm in vms:
            try:
                utils_test.check_dest_vm_network(vm, vms_ip[vm.name],
                                                 remote_host, username,
                                                 password)
            except exceptions.TestFail as detail:
                check_ip_failures.append(str(detail))
    else:
        for vm in vms:
            cp_mig.cleanup_dest_vm(vm, None, dest_uri)
        check_output(test, str(cp_mig.ret), params)
        test.fail("Migrate vms with storage copied failed.")
    if len(check_ip_failures):
        test.fail("Check IP failed:%s" % check_ip_failures)

    return cp_mig


def run(test, params, env):
    """
    Test migration with option --copy-storage-all or --copy-storage-inc.
    """
    vm = env.get_vm(params.get("migrate_main_vm"))
    disk_type = params.get("copy_storage_type", "file")
    if disk_type == "file":
        params['added_disk_type'] = "file"
    else:
        params['added_disk_type'] = "lvm"
    cp_mig = None
    primary_target = vm.get_first_disk_devices()["target"]
    file_path, file_size = vm.get_device_size(primary_target)
    # Convert to Gib
    file_size = int(file_size) // 1073741824

    # Set the pool target using the source of the first disk
    params["precreation_pool_target"] = os.path.dirname(file_path)

    remote_host = params.get("migrate_dest_host", "REMOTE.EXAMPLE")
    local_host = params.get("migrate_source_host", "LOCAL.EXAMPLE")
    remote_user = params.get("migrate_dest_user", "root")
    remote_passwd = params.get("migrate_dest_pwd")
    if remote_host.count("EXAMPLE") or local_host.count("EXAMPLE"):
        test.cancel("Config remote or local host first.")
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
    vms_ip = {}
    for vm in vms:
        if vm.is_dead():
            vm.start()
        vm.wait_for_login().close()
        vms_ip[vm.name] = vm.get_address()
    # Check if image pre-creation is supported.
    support_precreation = False
    try:
        if qemu_test("drive-mirror") and qemu_test("nbd-server"):
            support_precreation = True
    except exceptions.TestError as e:
        logging.debug(e)
    params["support_precreation"] = support_precreation
    # Abnormal parameters
    migrate_again = "yes" == params.get("migrate_again", "no")
    abnormal_type = params.get("abnormal_type")
    added_disks_list = []
    rdm = None
    src_libvirt_file = None
    try:
        rdm = utils_test.RemoteDiskManager(params)
        vgname = params.get("sm_vg_name", "SMTEST")
        pool_created = False

        if disk_type == "lvm":
            target1 = target2 = ""  # For cleanup
            # Create volume group with iscsi
            # For local, target is a device name
            target1 = utlv.setup_or_cleanup_iscsi(is_setup=True, is_login=True,
                                                  emulated_image="emulated-iscsi1")
            lv_utils.vg_create(vgname, target1)
            logging.debug("Created VG %s", vgname)
            # For remote, target is real target name
            target2, _ = utlv.setup_or_cleanup_iscsi(is_setup=True, is_login=False,
                                                     emulated_image="emulated-iscsi2")
            logging.debug("Created target: %s", target2)
            # Login on remote host
            remote_device = rdm.iscsi_login_setup(local_host, target2)
            if not rdm.create_vg(vgname, remote_device):
                test.error("Create VG %s on %s failed."
                           % (vgname, remote_host))

        all_disks = utlv.attach_disks(vm, file_path, vgname, params)
        # Reserve for cleanup
        added_disks_list = list(all_disks.keys())
        all_disks[file_path] = file_size
        logging.debug("All disks need to be migrated:%s", all_disks)

        if abnormal_type == "occupied_disk":
            occupied_path = rdm.occupy_space(disk_type, file_size,
                                             file_path, vgname, timeout=600)
        if abnormal_type != "not_exist_file":
            for disk, size in list(all_disks.items()):
                if disk == file_path:
                    if support_precreation:
                        pool_created = create_destroy_pool_on_remote(test, "create",
                                                                     params)
                        if not pool_created:
                            test.error("Create pool on remote " +
                                       "host '%s' failed."
                                       % remote_host)
                    else:
                        rdm.create_image("file", disk, size, None,
                                         None, img_frmt='qcow2')
                else:
                    sparse = False if disk_type == 'lvm' else True
                    rdm.create_image(disk_type, disk, size, vgname,
                                     os.path.basename(disk),
                                     sparse=sparse, timeout=120)

        fail_flag = False
        remove_dict = {
            "do_search": '{"%s": "ssh:/"}' % params.get("migrate_dest_uri")}
        src_libvirt_file = libvirt_config.remove_key_for_modular_daemon(
            remove_dict)
        try:
            logging.debug("Start migration...")
            cp_mig = copied_migration(test, vms, vms_ip, params)
            # Check the new disk can be working well with I/O after migration
            utils_disk.check_remote_vm_disks({'server_ip': remote_host,
                                              'server_user': remote_user,
                                              'server_pwd': remote_passwd,
                                              'vm_ip': vms_ip[vm.name],
                                              'vm_pwd': params.get('password')})

            if migrate_again:
                fail_flag = True
                test.fail("Migration succeed, but not expected!")
            else:
                return
        except exceptions.TestFail:
            if not migrate_again:
                raise

            if abnormal_type == "occupied_disk":
                rdm.remove_path(disk_type, occupied_path)
            elif abnormal_type == "not_exist_file":
                for disk, size in list(all_disks.items()):
                    if disk == file_path:
                        rdm.create_image("file", disk, size, None,
                                         None, img_frmt='qcow2')
                    else:
                        rdm.create_image(disk_type, disk, size, vgname,
                                         os.path.basename(disk))
            elif abnormal_type == "migration_interupted":
                params["thread_timeout"] = 120
            # Raise after cleanup
            if fail_flag:
                raise

            # Migrate it again to confirm failed reason
            params["status_error"] = "no"
            cp_mig = copied_migration(test, vms, vms_ip, params)
    finally:
        # Recover created vm
        if cp_mig:
            cp_mig.cleanup_dest_vm(vm, None, params.get("migrate_dest_uri"))
        if vm.is_alive():
            vm.destroy()

        if src_libvirt_file:
            src_libvirt_file.restore()

        if disks_count and vm.name == new_vm_name:
            vm.undefine()
        for disk in added_disks_list:
            if disk_type == 'file':
                utlv.delete_local_disk(disk_type, disk)
            else:
                lvname = os.path.basename(disk)
                utlv.delete_local_disk(disk_type, disk, vgname, lvname)
            rdm.remove_path(disk_type, disk)
        rdm.remove_path("file", file_path)
        if pool_created:
            pool_destroyed = create_destroy_pool_on_remote(test, "destroy", params)
            if not pool_destroyed:
                test.error("Destroy pool on remote host '%s' failed."
                           % remote_host)

        if disk_type == "lvm":
            rdm.remove_vg(vgname)
            rdm.iscsi_login_setup(local_host, target2, is_login=False)
            try:
                lv_utils.vg_remove(vgname)
            except Exception:
                pass    # let it go to confirm cleanup iscsi device
            utlv.setup_or_cleanup_iscsi(is_setup=False,
                                        emulated_image="emulated-iscsi1")
            utlv.setup_or_cleanup_iscsi(is_setup=False,
                                        emulated_image="emulated-iscsi2")
