import logging

from virttest import libvirt_vm
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml


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

    src_uri = "qemu:///system"
    dest_uri = libvirt_vm.complete_uri(params["server_ip"])

    vmxml_dict = {}

    migrate_setup = libvirt.MigrationTest()
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
        except Exception as info:
            test.fail(info)
        for vm in vm_list:
            if not migrate_setup.check_vm_state(vm.name, vm_state, dest_uri):
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
        for source_file in params.get("source_file_list", []):
            libvirt.delete_local_disk("file", path=source_file)

        if vmxml_dict:
            for key in vmxml_dict.keys():
                vmxml_dict[key].define()
