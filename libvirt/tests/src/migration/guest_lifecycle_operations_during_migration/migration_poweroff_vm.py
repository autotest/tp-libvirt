from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test poweroff vm during migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """

    def setup_poweroff_vm():
        """
        Setup for poweroff vm

        """
        vm_session = None

        test.log.info("Setup for poweroff vm.")
        migrate_desturi_port = params.get("migrate_desturi_port")
        migrate_desturi_type = params.get("migrate_desturi_type", "tcp")

        if migrate_desturi_type:
            migration_obj.conn_list.append(migration_base.setup_conn_obj(migrate_desturi_type, params, test))

        if migrate_desturi_port:
            migration_obj.remote_add_or_remove_port(migrate_desturi_port)

        libvirt.set_vm_disk(vm, params)
        if not vm.is_alive():
            vm.start()
        vm_session = vm.wait_for_login()
        params.update({'vm_session': vm_session})

    def verify_poweroff_vm():
        """
        Verify for poweroff vm
        """
        vm_state_dest = params.get("virsh_migrate_dest_state", "running")
        vm_state_src = params.get("virsh_migrate_src_state", "shut off")
        dest_uri = params.get("virsh_migrate_desturi")
        test.log.info("Verify for poweroff vm.")
        if virsh.domain_exists(vm.name, uri=dest_uri):
            if not libvirt.check_vm_state(vm.name, vm_state_dest, uri=dest_uri):
                test.fail("Migrated VM failed to be in %s state at destination" % vm_state_dest)
            test.log.info("Guest state is '%s' at destination is as expected", vm_state_dest)
        else:
            test.log.info("Guest don't exist on destination.")
        if not libvirt.check_vm_state(vm.name, vm_state_src, uri=migration_obj.src_uri):
            test.fail("Migrated VM failed to be in %s state at source" % vm_state_src)
        test.log.info("Guest state is '%s' at source is as expected", vm_state_dest)
        migration_obj.verify_default()

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection
    verify_test = eval("verify_%s" % test_case) if "verify_%s" % test_case in \
        locals() else migration_obj.verify_default

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
