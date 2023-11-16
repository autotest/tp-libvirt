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
    To verify that live migration with copying storage will fail when target
    image size is smaller than source image size.

    :param test: test object
    :param params: dictionary with the test parameters
    :param env: dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        server_ip = params.get("server_ip")
        server_user = params.get("server_user")
        server_pwd = params.get("server_pwd")
        disk_size = params.get("disk_size")

        migration_obj.setup_connection()
        remote_session = remote.remote_login("ssh", server_ip, "22",
                                             server_user, server_pwd,
                                             r'[$#%]')
        first_disk = vm.get_first_disk_devices()
        disk_path = first_disk["source"]
        utils_misc.make_dirs(os.path.dirname(disk_path), remote_session)
        libvirt_disk.create_disk(first_disk["type"], path=disk_path,
                                 size=disk_size, disk_format="qcow2",
                                 session=remote_session)
        remote_session.close()

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
        base_steps.cleanup_disks_remote(params, vm)
