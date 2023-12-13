# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Redhat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: Liping Cheng <lcheng@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

import re

from avocado.utils import process

from virttest import remote
from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base

src_image_info = None


def check_image_info(params, test):
    """
    Check image info

    :param params: Dictionary with the test parameters
    :param test: test object
    """
    disk_source_name = params.get("disk_source_name")
    disk_format = params.get("disk_format")

    cmd = "qemu-img info %s -U" % disk_source_name
    target_image_info = remote.run_remote_cmd(cmd, params,
                                              ignore_status=False).stdout_text.strip()
    global src_image_info
    for src_line in src_image_info.splitlines():
        if "disk size:" in src_line:
            continue
        if disk_format == "qcow2" and "file length:" in src_line:
            continue
        if (disk_format == "raw" and ("Format specific information:" in src_line or "extent size hint" in src_line)):
            continue
        if "virtual size:" in src_line or "file length:" in src_line:
            src_line = re.sub('\(.*?\)', '', src_line)
        if not re.search(src_line, target_image_info):
            test.fail(f"Not found '{src_line}' in {target_image_info}")


def run(test, params, env):
    """
    To verify that libvirt can create target image automatically when do live
    migration with copying storage.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        pool_name = params.get("target_pool_name")
        pool_type = params.get("target_pool_type")
        pool_target = params.get("target_pool_target")
        dest_uri = params.get("virsh_migrate_desturi")
        disk_format = params.get("disk_format")
        disk_source_name = params.get("disk_source_name")
        migrate_desturi_port = params.get("migrate_desturi_port")
        migrate_desturi_type = params.get("migrate_desturi_type", "tcp")

        test.log.info("Setup steps.")
        cmd = "qemu-img convert -f qcow2 -O %s %s %s" % (disk_format, blk_source, disk_source_name)
        process.run(cmd, shell=True)

        migration_obj.conn_list.append(migration_base.setup_conn_obj(migrate_desturi_type, params, test))
        migration_obj.remote_add_or_remove_port(migrate_desturi_port)
        libvirt.set_vm_disk(vm, params)

        global src_image_info
        cmd = "qemu-img info %s -U" % disk_source_name
        src_image_info = process.run(cmd, ignore_status=True, shell=True).stdout_text.strip()

        virsh.pool_create_as(pool_name, pool_type, pool_target, uri=dest_uri,
                             ignore_status=True, debug=True)

    def verify_test():
        """
        Verify steps

        """
        disk_source_name = params.get("disk_source_name")
        disk_format = params.get("disk_format")
        copy_storage_option = params.get("copy_storage_option")

        test.log.info("Verify steps.")
        migration_obj.verify_default()
        if "copy-storage-all" in copy_storage_option:
            check_image_info(params, test)

    def cleanup_test():
        """
        Cleanup steps

        """
        pool_name = params.get("target_pool_name")
        dest_uri = params.get("virsh_migrate_desturi")
        disk_source_name = params.get("disk_source_name")

        test.log.info("Cleanup steps.")
        virsh.pool_destroy(pool_name, ignore_status=True, debug=True, uri=dest_uri)
        cmd = "rm -rf %s" % disk_source_name
        process.run(cmd, shell=True)
        remote.run_remote_cmd(cmd, params)
        migration_obj.cleanup_connection()

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    first_disk = vm.get_first_disk_devices()
    blk_source = first_disk['source']
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        cleanup_test()
