import logging
from autotest.client.shared import error, ssh_key
from virttest import utils_test, remote
from virttest.utils_test import libvirt as utlv


def copied_migration(vms, params):
    """
    Migrate vms with storage copied.
    """
    dest_uri = params.get("migrate_dest_uri")
    remote_host = params.get("remote_ip")
    copy_option = params.get("copy_storage_option", "")
    username = params.get("remote_user")
    password = params.get("remote_pwd")
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
    vms = [vm]
    disk_type = params.get("copy_storage_type", "file")
    file_path, file_size = vm.get_device_size("vda")
    # Convert to Gib
    file_size = int(file_size) / 1073741824

    remote_host = params.get("remote_ip", "REMOTE.EXAMPLE")
    remote_user = params.get("remote_user", "root")
    remote_passwd = params.get("remote_pwd", "PASSWORD.EXAMPLE")
    if remote_host.count("EXAMPLE"):
        raise error.TestNAError("Config a remote host first.")
    # Config ssh autologin for it
    ssh_key.setup_ssh_key(remote_host, remote_user, remote_passwd, port=22)

    rdm = utils_test.RemoteDiskManager(params)
    try:
        rdm.create_image(disk_type, file_path, file_size)
        copied_migration(vms, params)
    finally:
        # Recover created vm
        if vm.is_alive():
            vm.destroy()
        rdm.remove_path(disk_type, file_path)
