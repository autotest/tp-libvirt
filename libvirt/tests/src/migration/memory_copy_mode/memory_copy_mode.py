from virttest import libvirt_version
from virttest import remote

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    Test live migration with precopy/postcopy.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_postcopy():
        """
        Verify for postcopy migration

        """
        test.log.info("Verify steps.")
        migration_obj.verify_default()

        if libvirt_version.version_compare(10, 1, 0):
            remote.run_remote_cmd("yum install lsof -y", params)
            # Check libvirt pass userfaultfd in vm namespace and qemu can access
            # this file correctly after postcopy migration on target host.
            cmd = "nsenter -a -t `pidof qemu-kvm` lsof /dev/userfaultfd"
            remote.run_remote_cmd(cmd, params, ignore_status=False)

    vm_name = params.get("migrate_main_vm")
    test_case = params.get('test_case', '')

    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    verify_test = eval("verify_%s" % test_case) if "verify_%s" % test_case in \
        locals() else migration_obj.verify_default

    try:
        migration_obj.setup_connection()
        # Monitor event on source/target host
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        verify_test()
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)
    finally:
        migration_obj.cleanup_connection()
