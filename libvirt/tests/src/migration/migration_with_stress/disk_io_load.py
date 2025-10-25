# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

from virttest import utils_package

from provider.migration import base_steps


def run(test, params, env):
    """
    This case is to verify that vm migration can succeed when there is heavy
    disk I/O load in vm

    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps for cases.")
        migration_obj.setup_connection()
        vm_session = vm.wait_for_login()
        if not utils_package.package_install("iozone", vm_session):
            test.error("Failed to install iozone on guest.")
        vm_session.cmd("nohup /opt/iozone/bin/iozone -a > /dev/null 2>&1 &")
        vm_session.close()

    def verify_test():
        """
        Verify steps for cases

        """
        dest_uri = params.get("virsh_migrate_desturi")

        test.log.info("Verify steps.")
        backup_uri, vm.connect_uri = vm.connect_uri, dest_uri
        vm.cleanup_serial_console()
        vm.create_serial_console()
        remote_vm_session = vm.wait_for_serial_login(timeout=360)
        remote_vm_dmesg = remote_vm_session.cmd_output("dmesg")
        if "I/O error" in remote_vm_dmesg:
            test.fail(f"Found I/O error in guest dmesg: {remote_vm_dmesg}")
        remote_vm_session.close()
        vm.connect_uri = backup_uri

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
