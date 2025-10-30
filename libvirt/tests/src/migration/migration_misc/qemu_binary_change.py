# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from avocado.utils import process

from provider.migration import base_steps


def run(test, params, env):
    """
    if qemu binary changes(e.g. chmod g+w /usr/libexec/qemu-kvm) during
    live migration with --persistent, the VM can migrate successfully.

    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps for cases.")
        migration_obj.setup_connection()
        cmd = ("nohup bash -c 'while true; do chmod g+w /usr/libexec/qemu-kvm; "
               "chmod g-w /usr/libexec/qemu-kvm; sleep 0.1; done' > /dev/null 2>&1 &")
        process.run(cmd, shell=True)

    def verify_test():
        """
        Verify steps for cases

        """
        test.log.info("Verify steps for cases.")
        cmd = "pkill -f 'while true; do chmod'"
        process.run(cmd, shell=True)
        migration_obj.verify_default()

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
