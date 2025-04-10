#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0

#   Author: Liping Cheng<lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest.utils_libvirt import libvirt_monitor

from provider.migration import base_steps


def run(test, params, env):
    """
    To verify that domain job info can be queried during and after live
    migration with copy storage.

    """
    def verify_test():
        """
        Verify test

        """
        expected_domjobinfo_complete = eval(params.get("expected_domjobinfo_complete"))
        remote_ip = params.get("server_ip")

        test.log.info("Verify test.")
        libvirt_monitor.check_domjobinfo_output(vm_name,
                                                expected_domjobinfo_complete=expected_domjobinfo_complete,
                                                options="--completed",
                                                remote_ip=remote_ip)
        migration_obj.verify_default()

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        migration_obj.setup_connection()
        base_steps.prepare_disks_remote(params, vm)
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
        base_steps.cleanup_disks_remote(params, vm)
