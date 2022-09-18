from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test network bandwidth - precopy.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_set_bandwidth_when_vm_shutoff():
        """
        Setup bandwidth when vm shutoff

        """
        test.log.debug("Setup bandwidth when vm shutoff.")
        migrate_desturi_port = params.get("migrate_desturi_port")
        migrate_desturi_type = params.get("migrate_desturi_type", "tcp")
        compared_value = params.get("compared_value")

        migration_obj.conn_list.append(migration_base.setup_conn_obj(migrate_desturi_type, params, test))
        migration_obj.remote_add_or_remove_port(migrate_desturi_port)
        libvirt.set_vm_disk(vm, params)
        if vm.is_alive():
            vm.destroy()
        virsh_args = {"debug": True, "ignore_status": False}
        virsh.migrate_setspeed(vm_name, compared_value, **virsh_args)
        virsh.migrate_getspeed(vm_name, debug=True)
        vm.start()
        vm.wait_for_login().close()

    test_case = params.get('test_case', '')
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    params.update({'vm_obj': vm})
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
