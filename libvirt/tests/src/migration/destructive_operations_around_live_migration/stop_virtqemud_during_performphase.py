# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import remote
from virttest import utils_libvirtd
from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps


def run(test, params, env):
    """
    This case is to test stopping virtqemud on the source/destination
    during migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def verify_test():
        """
        Verify migration result

        """
        server_ip = params.get("server_ip")
        server_user = params.get("server_user", "root")
        server_pwd = params.get("server_pwd")
        dest_uri = params.get("virsh_migrate_desturi")
        expected_src_state = params.get("expected_src_state")
        expected_dest_state = params.get("expected_dest_state")
        test_case = params.get("test_case")

        func_returns = dict(migration_obj.migration_test.func_ret)
        migration_obj.migration_test.func_ret.clear()
        test.log.debug("Migration returns function results: %s", func_returns)
        if test_case == "stop_dst_virtqemud":
            dst_session = remote.wait_for_login('ssh', server_ip, '22',
                                                server_user, server_pwd,
                                                r"[\#\$]\s*$")
            utils_libvirtd.Libvirtd("virtqemud", session=dst_session).start()
            dst_session.close()
        if expected_dest_state and expected_dest_state == "nonexist":
            virsh.domstate(vm_name, uri=dest_uri, debug=True)
            if virsh.domain_exists(vm_name, uri=dest_uri):
                test.fail("The domain on target host is found, but expected not.")
        if test_case == "stop_src_virtqemud":
            utils_libvirtd.Libvirtd("virtqemud").start()
        if expected_src_state:
            if not libvirt.check_vm_state(vm.name, expected_src_state, uri=migration_obj.src_uri):
                test.fail("Migrated VM failed to be in %s state at source." % expected_src_state)

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        migration_obj.run_migration()
        verify_test()
        migration_obj.run_migration_again()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
