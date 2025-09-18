# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import libvirt_version
from virttest import virsh

from provider.migration import base_steps


def run(test, params, env):
    """
    To verify that bandwidth limit can take effect for storage copying during
    migration.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_setspeed_before_migration():
        """
        Setup for setspeed_before_migration

        """
        bandwidth = params.get("bandwidth")

        test.log.info("Setup bandwidth limit before migration.")
        migration_obj.setup_connection()
        virsh.migrate_setspeed(vm_name, bandwidth, debug=True)

    def cleanup_test():
        """
        Cleanup steps

        """
        migration_obj.cleanup_connection()
        base_steps.cleanup_disks_remote(params, vm)

    libvirt_version.is_libvirt_feature_supported(params)
    set_bandwidth = params.get('set_bandwidth', '')
    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    params.update({'vm_obj': vm})
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % set_bandwidth) if "setup_%s" % set_bandwidth in \
        locals() else migration_obj.setup_connection

    try:
        setup_test()
        base_steps.prepare_disks_remote(params, vm)
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
