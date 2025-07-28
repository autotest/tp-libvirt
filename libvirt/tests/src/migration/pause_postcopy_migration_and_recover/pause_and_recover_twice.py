from virttest import libvirt_version
from virttest.utils_libvirt import libvirt_network

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test postcopy migration, then pause it, then recover it and make recovery
    fail, then recover it again.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps for cases

        """
        test.log.info("Setup for cases")
        if recover_failed_reason == "network_issue":
            tcp_config_list = eval(params.get("tcp_config_list"))
            libvirt_network.change_tcp_config(tcp_config_list, params)
        migration_obj.setup_connection()

    def recover_for_resume():
        test.log.info("Recover for resume")
        if recover_failed_reason == "network_issue":
            libvirt_network.cleanup_firewall_rule(params)
        elif recover_failed_reason == "proxy_issue":
            base_steps.recreate_conn_objs(params)

    def cleanup_test():
        """
        Cleanup steps for cases

        """
        desturi = params.get("virsh_migrate_desturi")

        test.log.info("Cleanup for cases")
        if recover_failed_reason == "network_issue":
            recover_tcp_config_list = eval(params.get("recover_tcp_config_list"))
            libvirt_network.cleanup_firewall_rule(params)
            libvirt_network.change_tcp_config(recover_tcp_config_list, params)
        migration_obj.cleanup_connection()

    libvirt_version.is_libvirt_feature_supported(params)

    recover_failed_reason = params.get('recover_failed_reason', '')
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    params.update({"migration_obj": migration_obj})

    try:
        setup_test()
        migration_obj.run_migration()
        migration_base.resume_migration(params)
        recover_for_resume()
        migration_base.resume_migration(params, resume_again=True)
        migration_obj.verify_default()
    finally:
        cleanup_test()
