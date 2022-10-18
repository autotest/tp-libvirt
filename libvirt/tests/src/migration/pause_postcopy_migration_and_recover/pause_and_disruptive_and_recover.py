from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    Test postcopy migration, then pause it, then do some operations.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def cleanup_kill_dest_qemu():
        """
        Cleanup steps for kill_dest_qemu
        """
        test.log.info("Cleanup steps for kill_dest_qemu.")
        if libvirt.check_vm_state(vm.name, "paused"):
            virsh.destroy(vm_name, ignore_status=True, debug=True)
            virsh.start(vm_name, ignore_status=True, debug=True)
        migration_obj.cleanup_connection()

    disruptive_operations = params.get("disruptive_operations")
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    params.update({"migration_obj": migration_obj})

    cleanup_test = eval("cleanup_%s" % disruptive_operations) if "cleanup_%s" % disruptive_operations in \
        locals() else migration_obj.cleanup_connection

    try:
        migration_obj.setup_connection()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
