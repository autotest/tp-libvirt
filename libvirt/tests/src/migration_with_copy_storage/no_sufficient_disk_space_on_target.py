# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import os

from virttest import remote
from virttest import utils_misc

from virttest.utils_libvirt import libvirt_disk

from provider.migration import base_steps


def run(test, params, env):
    """
    To verify that live migration with copying storage will fail when there
    is no sufficient disk space on target host.

    :param test: test object
    :param params: dictionary with the test parameters
    :param env: dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        disk_size = params.get("disk_size")
        block_path = params.get("block_path")

        migration_obj.setup_connection()
        remote_session = remote.remote_login("ssh", server_ip, "22",
                                             server_user, server_pwd,
                                             r'[$#%]')
        utils_misc.make_dirs(disk_path, remote_session)
        remote_session.cmd(f"rm -rf {block_path}")
        libvirt_disk.create_disk(first_disk["type"], path=block_path,
                                 size=disk_size, disk_format="qcow2",
                                 extra="-o preallocation=falloc",
                                 session=remote_session)
        remote_session.cmd(f"losetup /dev/loop0 {block_path}")
        remote_session.cmd("mkfs.ext3 /dev/loop0")
        remote_session.cmd(f"mount /dev/loop0 {disk_path}")

        _, file_size = vm.get_device_size(first_disk["target"])
        libvirt_disk.create_disk(first_disk["type"], path=disk_name,
                                 size=file_size, disk_format="qcow2",
                                 session=remote_session)
        remote_session.close()

    def cleanup_test():
        """
        Cleanup steps

        """
        block_path = params.get("block_path")

        migration_obj.cleanup_connection()
        remote_session = remote.remote_login("ssh", server_ip, "22",
                                             server_user, server_pwd,
                                             r'[$#%]')
        remote_session.cmd(f"umount {disk_path}")
        remote_session.cmd("losetup -d /dev/loop0")
        remote_session.cmd(f"rm -rf {block_path}")
        remote_session.close()

    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    first_disk = vm.get_first_disk_devices()
    disk_name = first_disk["source"]
    disk_path = os.path.dirname(disk_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
