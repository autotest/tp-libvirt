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
from virttest import utils_package

from provider.migration import base_steps


def run(test, params, env):
    """
    To verify that vm migration can succeed when there is heavy disk I/O load
    in vm.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps.")
        migration_obj.setup_connection()
        session = vm.wait_for_login()
        utils_package.package_install(["gcc", "fio"], session, 360)
        shell_file = "/tmp/fio_test.sh"
        fio_cmd = ['while true',
                   'do',
                   '    fio -name=aaa -direct=1 -iodepth=32 -rw=randrw -ioengine=libaio -bs=16k -size=1G -numjobs=2 -group_reporting -directory=/ &>/dev/null',
                   'done']
        remote_file = remote.RemoteFile(vm.get_address(), 'scp', 'root',
                                        params.get('password'), 22,
                                        shell_file)
        remote_file.truncate()
        remote_file.add(fio_cmd)
        session.cmd('chmod 777 %s' % shell_file)
        session.cmd('%s &' % shell_file)
        session.close()

    def verify_test():
        """
        Verify steps

        """
        dest_uri = params.get("virsh_migrate_desturi")

        test.log.info("Verify steps.")
        backup_uri, vm.connect_uri = vm.connect_uri, dest_uri
        remote_vm_session = vm.wait_for_serial_login(timeout=360, recreate_serial_console=True)
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
        base_steps.prepare_disks_remote(params, vm)
        migration_obj.run_migration()
        verify_test()
    finally:
        migration_obj.cleanup_connection()
        base_steps.cleanup_disks_remote(params, vm)
