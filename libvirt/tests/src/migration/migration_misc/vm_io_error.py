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

from avocado.utils import process

from virttest import remote
from virttest import utils_disk
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base


def prepare_second_disk(params, test):
    """
    Prepare second disk for the VM

    :param params: Dictionary with the test parameters
    :param test: test object
    """
    loop_disk = params.get("loop_disk")
    loop_export_dir = params.get("loop_export_dir")
    loop_mnt_dir = params.get("loop_mnt_dir")

    if not os.path.exists(loop_export_dir):
        os.mkdir(loop_export_dir)
    if not os.path.exists(loop_mnt_dir):
        os.mkdir(loop_mnt_dir)

    process.run(f"truncate -s 30M {loop_disk}", shell=True, ignore_status=False)
    process.run(f"losetup /dev/loop0 {loop_disk}", shell=True, ignore_status=False)
    process.run(f"mkfs.ext4 /dev/loop0", shell=True, ignore_status=False)
    process.run(f"mount /dev/loop0 {loop_export_dir}", shell=True, ignore_status=False)
    libvirt.setup_or_cleanup_nfs(True, mount_dir=loop_mnt_dir, is_mount=True, export_dir=loop_export_dir)

    libvirt_disk.create_disk("file", disk_format="qcow2", path=params.get("second_disk"), size="100M")

    cmd = f"mkdir -p {loop_mnt_dir}"
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    remote_session = remote.remote_login("ssh", params.get("server_ip"), "22",
                                         params.get("server_user"),
                                         params.get("server_pwd"),
                                         r'[$#%]')
    utils_misc.make_dirs(loop_mnt_dir, remote_session)
    utils_disk.mount((params.get("client_ip") + ":" + loop_export_dir), loop_mnt_dir, session=remote_session)
    remote_session.close()


def run(test, params, env):
    """
    This case is to verify that if vm I/O error occurs during migration,
    migration will succeed or fail depending on the vm disk configuration,
    migration flag, etc

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        test.log.info("Setup steps for cases.")
        prepare_second_disk(params, test)
        migration_obj.setup_connection()

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", eval(params.get("disk_dict"))))
        vmxml.sync()
        vm.start()
        vm.wait_for_login().close()

    def cleanup_test():
        """
        Cleanup steps for cases

        """
        test.log.info("Cleanup steps for cases.")
        loop_disk = params.get("loop_disk")
        loop_export_dir = params.get("loop_export_dir")
        loop_mnt_dir = params.get("loop_mnt_dir")
        client_ip = params.get("client_ip")

        migration_obj.cleanup_connection()
        remote_session = remote.remote_login("ssh", params.get("server_ip"), "22",
                                             params.get("server_user"),
                                             params.get("server_pwd"),
                                             r'[$#%]')
        utils_disk.umount(f"{client_ip}:{loop_export_dir}", loop_mnt_dir, session=remote_session)
        remote_session.close()
        remote.run_remote_cmd("rm -rf %s" % loop_mnt_dir, params)

        process.run(f"umount {loop_mnt_dir}", shell=True, verbose=True)
        process.run(f"exportfs -r", shell=True)
        process.run(f"umount {loop_export_dir}", shell=True)
        process.run(f"losetup -d /dev/loop0", shell=True)

        utils_misc.safe_rmdir(loop_mnt_dir)
        utils_misc.safe_rmdir(loop_export_dir)
        process.run(f"rm -rf {loop_disk}", shell=True, ignore_status=True)

    vm_name = params.get("migrate_main_vm")
    virsh_session = None
    remote_virsh_session = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    params.update({"migration_obj": migration_obj})

    try:
        setup_test()
        virsh_session, remote_virsh_session = migration_base.monitor_event(params)
        migration_obj.run_migration()
        migration_obj.verify_default()
        migration_base.check_event_output(params, test, virsh_session, remote_virsh_session)
    finally:
        cleanup_test()
