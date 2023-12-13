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

from virttest import remote
from virttest import utils_disk
from virttest import virsh

from virttest.utils_test import libvirt

from provider.migration import base_steps


def setup_block_device_on_remote(params):
    """
    Setup block device on remote host

    :param params: Dictionary with the test parameters
    """
    cmd = "modprobe scsi_debug lbpu=1 lbpws=1 dev_size_mb=2048"
    remote.run_remote_cmd(cmd, params, ignore_status=False)


def cleanup_block_device_on_remote(params):
    """
    Cleanup block device on remote host

    :param params: Dictionary with the test parameters
    """
    ret = remote.run_remote_cmd("lsscsi | grep scsi_debug", params, ignore_status=False)
    if ret.exit_status == 0:
        scsi_addr_pattern = '[0-9]+:[0-9]+:[0-9]+:[0-9]+'
        for addr in re.findall(scsi_addr_pattern, ret.stdout_text):
            remote.run_remote_cmd("echo 1>/sys/class/scsi_device/{}/device/delete".format(addr),
                                  params, ignore_status=False)

        remote.run_remote_cmd("modprobe -r scsi_debug", params, ignore_status=False)


def run(test, params, env):
    """
    To verify that live migration with copying storage can succeed for block
    disk.

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps.")
        attach_disk_args = params.get("attach_disk_args")
        scsi_disk_option = params.get("scsi_disk_option")
        target_type = params.get("target_type")

        migration_obj.setup_connection()
        base_steps.prepare_disks_remote(params, vm)
        setup_block_device_on_remote(params)

        disk_source = libvirt.create_scsi_disk(scsi_disk_option)
        virsh.attach_disk(vm_name, disk_source, target_type,
                          attach_disk_args, debug=True, ignore_status=False)
        vm.start()
        vm_session = vm.wait_for_login()
        utils_disk.linux_disk_check(vm_session, target_type)
        vm_session.close()

    def cleanup_test():
        """
        Cleanup steps

        """
        test.log.info("Cleanup steps.")
        migration_obj.cleanup_connection()
        libvirt.delete_scsi_disk()
        cleanup_block_device_on_remote(params)
        base_steps.cleanup_disks_remote(params, vm)

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        cleanup_test()
