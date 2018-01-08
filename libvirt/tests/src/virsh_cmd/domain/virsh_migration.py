import logging

from virttest import libvirt_vm
from virttest import nfs
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.utils_misc import SELinuxBoolean


def run(test, params, env):
    """
    Test KVM migration scenarios
    """
    migrate_options = params.get("migrate_options", "")
    migrate_postcopy = params.get("migrate_postcopy", "")
    migrate_dest_ip = params.get("migrate_dest_host")
    nfs_mount_path = params.get("nfs_mount_dir")
    migrate_start_state = params.get("migrate_start_state", "paused")
    postcopy_func = None
    if migrate_postcopy:
        postcopy_func = virsh.migrate_postcopy
    migrate_type = params.get("migrate_type", "orderly")
    vm_state = params.get("migrate_vm_state", "running")
    ping_count = int(params.get("ping_count", 15))

    vms = params.get("vms").split()
    vm_list = env.get_all_vms()

    # Params to update disk using shared storage
    params["disk_type"] = "file"
    params["disk_source_protocol"] = "netfs"
    params["mnt_path_name"] = nfs_mount_path

    # Params for NFS and SSH setup
    params["server_ip"] = migrate_dest_ip
    params["server_user"] = "root"
    params["server_pwd"] = params.get("migrate_dest_pwd")
    params["client_ip"] = params.get("migrate_source_host")
    params["client_user"] = "root"
    params["client_pwd"] = params.get("migrate_source_pwd")
    params["nfs_client_ip"] = migrate_dest_ip
    params["nfs_server_ip"] = params.get("migrate_source_host")

    # Params to enable SELinux boolean on remote host
    params["remote_boolean_varible"] = "virt_use_nfs"
    params["local_boolean_varible"] = "virt_use_nfs"
    params["remote_boolean_value"] = "on"
    params["local_boolean_value"] = "on"
    params["set_sebool_remote"] = "yes"
    params["set_sebool_local"] = "yes"

    src_uri = "qemu:///system"
    dest_uri = libvirt_vm.complete_uri(params["server_ip"])

    vmxml_dict = {}
    # Backup the SELinux status on local host for recovering
    local_selinux_bak = params.get("selinux_status_bak")

    # Configure NFS client on remote host
    nfs_client = nfs.NFSClient(params)
    nfs_client.setup()

    logging.info("Enable virt NFS SELinux boolean on target host.")
    seLinuxBool = SELinuxBoolean(params)
    seLinuxBool.setup()

    # Permit iptables to permit 49152-49216 ports to libvirt for
    # migration and if arch is ppc with power8 then switch off smt
    # will be taken care in remote machine for migration to succeed
    migrate_setup = libvirt.MigrationTest()
    migrate_setup.migrate_pre_setup(dest_uri, params)

    try:
        for vm in vm_list:
            vmxml_dict[vm.name] = vm_xml.VMXML.new_from_dumpxml(vm.name)
            params["source_dist_img"] = "%s-nfs-img" % vm.name
            if vm.is_alive():
                vm.destroy()
            libvirt.set_vm_disk(vm, params)
            migrate_setup.ping_vm(vm, test, params, ping_count=ping_count)
        try:
            migrate_setup.do_migration(vm_list, src_uri, dest_uri,
                                       migrate_type, migrate_options,
                                       func=postcopy_func,
                                       migrate_start_state=migrate_start_state)
        except Exception, info:
            test.fail(info)
        for vm in vm_list:
            if not migrate_setup.check_vm_state(vm, vm_state, dest_uri):
                test.fail("Migrated VMs failed to be in %s state at "
                          "destination" % vm_state)
            logging.info("Guest state is '%s' at destination is as expected",
                         vm_state)
            migrate_setup.ping_vm(vm, test, params, uri=dest_uri, ping_count=ping_count)
    finally:
        logging.debug("cleanup the migration setup in source/destination")
        for vm in vm_list:
            if migrate_setup:
                migrate_setup.cleanup_dest_vm(vm, src_uri, dest_uri)
            if vm.exists() and vm.is_persistent():
                vm.undefine()
            if vm.is_alive():
                vm.destroy()
        # clean up of pre migration setup for local machine
        if migrate_setup:
            migrate_setup.migrate_pre_setup(src_uri, params, cleanup=True)
        for source_file in params.get("source_file_list", []):
            libvirt.delete_local_disk("file", path=source_file)
        exp_dir = params.get("export_dir")
        mount_dir = params.get("mnt_path_name")
        libvirt.setup_or_cleanup_nfs(False, export_dir=exp_dir,
                                     mount_dir=mount_dir,
                                     restore_selinux=local_selinux_bak,
                                     rm_export_dir=False)
        if seLinuxBool:
            seLinuxBool.cleanup(True)

        if vmxml_dict:
            for key in vmxml_dict.keys():
                vmxml_dict[key].define()
