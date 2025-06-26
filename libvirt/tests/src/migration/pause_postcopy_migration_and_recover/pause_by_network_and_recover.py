from virttest import libvirt_version
from virttest import virsh

from virttest.utils_libvirt import libvirt_network
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test postcopy migration, then pause migration by network issue, then recover migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_test():
        """
        Verify steps

        """
        dest_uri = params.get("virsh_migrate_desturi")
        dominfo_check = params.get("dominfo_check")

        test.log.info("Verify for pause_and_network_and_recover.")
        # check domain persistence
        if dominfo_check:
            dominfo = virsh.dominfo(vm_name, ignore_status=True, debug=True, uri=dest_uri)
            libvirt.check_result(dominfo, expected_match=dominfo_check)

        migration_obj.verify_default()
        # check qemu-guest-agent command
        result = virsh.domtime(vm_name, ignore_status=True, debug=True, uri=dest_uri)
        libvirt.check_exit_status(result)

        # Check event output
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)

    libvirt_version.is_libvirt_feature_supported(params)

    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")
    tcp_config_list = eval(params.get("tcp_config_list"))
    recover_tcp_config_list = eval(params.get("recover_tcp_config_list"))
    remove_firewall_rule = False

    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    params.update({"migration_obj": migration_obj})

    try:
        base_steps.setup_network_data_transport(params)
        libvirt_network.change_tcp_config(tcp_config_list, params)
        migration_obj.setup_connection()
        # Monitor event on source/target host
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        migration_base.do_common_check(params)
        libvirt_network.cleanup_firewall_rule(params)
        remove_firewall_rule = True
        if migrate_again:
            migration_obj.run_migration_again()
        verify_test()
    finally:
        if not remove_firewall_rule:
            libvirt_network.cleanup_firewall_rule(params)
        libvirt_network.change_tcp_config(recover_tcp_config_list, params)
        migration_obj.cleanup_connection()
