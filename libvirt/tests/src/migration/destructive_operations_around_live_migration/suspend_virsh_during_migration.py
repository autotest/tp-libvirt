# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from provider.migration import base_steps
from provider.migration import migration_base


def run(test, params, env):
    """
    This case is to verify that if virsh(management app) is suspended during
    migration, migration will succeed.

    """
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    virsh_session = None
    remote_virsh_session = None

    try:
        migration_obj.setup_connection()
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        migration_obj.verify_default()
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)
    finally:
        migration_obj.cleanup_connection()
