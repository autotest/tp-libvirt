from virttest import libvirt_version

from virttest.utils_libvirt import libvirt_network

from provider.migration import base_steps


def run(test, params, env):
    """
    Do postcopy migration, then make migration become "unattended migration".
    Then do disruptive operations during unattended migration. Then try to
    recover migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_pause_by_network():
        """
        Setup for pause_by_network

        """
        test.log.info("Setup for pause_by_network.")
        tcp_config_list = eval(params.get("tcp_config_list"))
        libvirt_network.change_tcp_config(tcp_config_list)
        migration_obj.setup_connection()

    def cleanup_pause_by_network():
        """
        Cleanup for pause_by_network

        """
        test.log.info("Cleanup for pause_by_network")
        recover_tcp_config_list = eval(params.get("recover_tcp_config_list"))
        libvirt_network.change_tcp_config(recover_tcp_config_list)
        migration_obj.cleanup_connection()

    libvirt_version.is_libvirt_feature_supported(params)

    vm_name = params.get("migrate_main_vm")
    disruptive_operations = params.get("disruptive_operations")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    params.update({"migration_obj": migration_obj})

    setup_test = eval("setup_%s" % disruptive_operations) if "setup_%s" % disruptive_operations in \
        locals() else migration_obj.setup_connection
    cleanup_test = eval("cleanup_%s" % disruptive_operations) if "cleanup_%s" % disruptive_operations in \
        locals() else migration_obj.cleanup_connection

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
