from virttest.utils_libvirt import libvirt_network

from provider.migration import base_steps


def run(test, params, env):
    """
    Test postcopy migration, then pause it, then recover it and make recovery
    fail, then recover it again.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_network_issue():
        """
        Setup for network issue

        """
        test.log.info("Setup for network issue")
        tcp_config_list = eval(params.get("tcp_config_list"))
        libvirt_network.change_tcp_config(tcp_config_list)
        migration_obj.setup_connection()

    def cleanup_network_issue():
        """
        Cleanup for network issue

        """
        test.log.info("Cleanup for network issue")
        recover_tcp_config_list = eval(params.get("recover_tcp_config_list"))
        libvirt_network.change_tcp_config(recover_tcp_config_list)
        migration_obj.cleanup_connection()

    recover_failed_reason = params.get('recover_failed_reason', '')
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    params.update({"migration_obj": migration_obj})

    setup_test = eval("setup_%s" % recover_failed_reason) if "setup_%s" % recover_failed_reason in \
        locals() else migration_obj.setup_connection
    cleanup_test = eval("cleanup_%s" % recover_failed_reason) if "cleanup_%s" % recover_failed_reason in \
        locals() else migration_obj.cleanup_connection

    try:
        setup_test()
        migration_obj.setup_connection()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
