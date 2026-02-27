import ast
import os

from virttest import virsh
from virttest import remote
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.virtual_disk import disk_base


def run(test, params, env):
    """
    Test migration after disk hotplug.
    """

    def setup_test():
        """
        Setup steps before migration
        """
        migration_obj.setup_connection()
        test.log.info("Prepare the disk xml and hotplug.")
        new_image_path = libvirt.create_local_disk(
                                                   disk_type,
                                                   os.path.join(mount_path_name, "test.img")
                                                   )
        disk_xml, _ = disk_obj.prepare_disk_obj(disk_type, disk_dict, new_image_path)
        test.log.debug("Disk XML for hotplug:\n%s" % disk_xml)
        virsh.attach_device(vm_name, disk_xml.xml, debug=True, ignore_status=False)
        vm_disks = vm.get_blk_devices().keys()
        if disk_target not in vm_disks:
            test.fail("Failed to hotplug disk %s to VM" % disk_target)
        test.log.info("Successfully hotplugged disk %s" % disk_target)

    def cleanup_test():
        """
        Cleanup steps after test
        """
        test.log.info("Cleanup steps for migration after disk hotplug.")
        migration_obj.cleanup_connection()
        disk_obj.cleanup_disk_preparation(disk_type)

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)

    disk_type = params.get("disk_type", "file")
    disk_target = params.get("disk_target", "vdb")
    mount_path_name = params.get("mount_path_name", "/var/lib/libvirt/migrate")
    disk_dict = ast.literal_eval(params.get("disk_dict", "{}"))

    migration_obj = base_steps.MigrationBase(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    base_steps.sync_cpu_for_mig(params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
        remote_cmd = "virsh detach-disk %s %s" % (vm_name, disk_target)
        remote.run_remote_cmd(remote_cmd, params, ignore_status=False)
        migration_obj.run_migration_back()
        all_vm_disks = vm.get_blk_devices().keys()
        if disk_target in all_vm_disks:
            test.fail("Disk %s still exists after migration back." % disk_target)
    finally:
        cleanup_test()
