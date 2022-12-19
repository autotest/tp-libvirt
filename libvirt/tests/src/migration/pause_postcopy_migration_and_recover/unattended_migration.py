from virttest import libvirt_version
from virttest import virsh

from virttest.utils_libvirt import libvirt_network
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test postcopy migration, then make migration become "unattended migration".
    Then wait for unattended migration to finish..

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_kill_src_virtqemud():
        """
        Setup for kill_src_virtqemud

        """
        test.log.info("Setup for kill_src_virtqemud.")
        virsh_migrate_options = params.get("virsh_migrate_options")
        status_error = "yes" == params.get("status_error", "no")
        if "--p2p" not in virsh_migrate_options:
            # In kill_src_virtqemud.non_p2p case, migration result status is 0. And stdout contains
            # "Migration: [  3 %]error: Disconnected from qemu:///system due to end of file0 %]".
            # So need to set status_error to 'no'.
            params.update({"status_error": "no"})
        migration_obj.setup_connection()

    def setup_network_issue_libvirt_layer():
        """
        Setup for network_issue_libvirt_layer

        """
        test.log.info("Setup for network_issue_libvirt_layer.")
        tcp_config_list = eval(params.get("tcp_config_list"))
        libvirt_network.change_tcp_config(tcp_config_list)
        migration_obj.setup_connection()

    def verify_test():
        """
        Verify steps

        """
        dest_uri = params.get("virsh_migrate_desturi")
        dominfo_check = params.get("dominfo_check")
        migration_options = params.get("migration_options")

        # check domain persistence
        if dominfo_check:
            dominfo = virsh.dominfo(vm_name, ignore_status=True, debug=True, uri=dest_uri)
            libvirt.check_result(dominfo, expected_match=dominfo_check)

        if migration_options == "with_undefinesource_persistent":
            virsh_migrate_src_state = params.get("virsh_migrate_src_state")
            func_returns = dict(migration_obj.migration_test.func_ret)
            migration_obj.migration_test.func_ret.clear()
            test.log.debug("Migration returns function results:%s", func_returns)
            if not libvirt.check_vm_state(vm_name, virsh_migrate_src_state, uri=migration_obj.src_uri):
                test.fail("Migrated VMs failed to be in %s state at source" % virsh_migrate_src_state)
            if int(migration_obj.migration_test.ret.exit_status) == 0:
                migration_obj.migration_test.post_migration_check([vm], params,
                                                                  dest_uri=dest_uri)
        else:
            migration_obj.verify_default()

    def cleanup_network_issue_libvirt_layer():
        """
        Cleanup for network_issue_libvirt_layer

        """
        test.log.info("Cleanup for network_issue_libvirt_layer")
        recover_tcp_config_list = eval(params.get("recover_tcp_config_list"))
        libvirt_network.change_tcp_config(recover_tcp_config_list)
        migration_obj.cleanup_connection()

    libvirt_version.is_libvirt_feature_supported(params)

    vm_name = params.get("migrate_main_vm")
    make_unattended = params.get("make_unattended")

    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    params.update({"migration_obj": migration_obj})

    setup_test = eval("setup_%s" % make_unattended) if "setup_%s" % make_unattended in \
        locals() else migration_obj.setup_connection
    cleanup_test = eval("cleanup_%s" % make_unattended) if "cleanup_%s" % make_unattended in \
        locals() else migration_obj.cleanup_connection

    try:
        setup_test()
        # Monitor event on source/target host
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        params.update({"virsh_session": virsh_session})
        params.update({"remote_virsh_session": remote_virsh_session})
        migration_obj.run_migration()
        verify_test()
    finally:
        cleanup_test()
