from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test postcopy migration, then pause migration by UNIX proxy issue, then recover migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_test():
        """
        Verify for pause_by_proxy_and_recover

        """
        dest_uri = params.get("virsh_migrate_desturi")
        dominfo_check = params.get("dominfo_check")

        # check domain persistence
        if dominfo_check:
            dominfo = virsh.dominfo(vm_name, ignore_status=True, debug=True, uri=dest_uri)
            libvirt.check_result(dominfo, expected_match=dominfo_check)

        # check qemu-guest-agent command
        result = virsh.domtime(vm_name, ignore_status=True, debug=True, uri=dest_uri)
        libvirt.check_exit_status(result)

        migration_obj.verify_default()

        # Check event output
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)

    vm_name = params.get("migrate_main_vm")
    migrate_again = "yes" == params.get("migrate_again", "no")

    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    params.update({"migration_obj": migration_obj})

    try:
        migration_obj.setup_connection()
        # Monitor event on source/target host
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        if migrate_again:
            migration_obj.run_migration_again()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
