import re

from virttest import libvirt_version
from virttest import virsh

from provider.migration import base_steps

event_session = None


def run(test, params, env):
    """
    Test live migration with precopy/postcopy.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_postcopy_migration():
        """
        Setup for postcopy migration
        """
        expected_event_src = params.get("expected_event_src")
        if expected_event_src:
            cmd = "event --loop --all"
            global event_session
            event_session = virsh.VirshSession(virsh_exec=virsh.VIRSH_EXEC,
                                               auto_close=True)
            event_session.sendline(cmd)
        migration_obj.setup_connection()

    def verify_postcopy_migration():
        """
        Verify for postcopy migration
        """
        expected_event_src = params.get("expected_event_src")
        if expected_event_src:
            global event_session
            src_output = event_session.get_stripped_output()
            test.log.debug("Event output on source: %s", src_output)
            if not re.findall(expected_event_src, src_output):
                test.fail("Unable to find event {}".format(expected_event_src))
        migration_obj.verify_default()

    libvirt_version.is_libvirt_feature_supported(params)

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
