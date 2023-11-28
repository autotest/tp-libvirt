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

from virttest import data_dir
from virttest import remote
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml

from provider.migration import base_steps
from provider.migration import migration_base


def update_vm_with_additional_disk(disk_dict, disk_format, vm_name, disk_path=None):
    """
    Update vm with additional disk

    :param disk_dict: disk parameter
    :param disk_format: disk format
    :param vm_name: vm name
    :param disk_path: disk path
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if disk_path:
        disk_dict.update({"source": {"attrs": {"file": "%s" % disk_path}}})
        if os.path.exists(disk_path):
            os.remove(disk_path)
        libvirt_disk.create_disk("file", disk_format=disk_format, path=disk_path)
    vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", disk_dict))
    vmxml.sync()


def prepare_disk_local(blk_source, vm_name):
    """
    Prepare disk on local host

    :param blk_source: first disk path
    :param vm_name: vm name
    """
    default_image_path = os.path.join(data_dir.get_data_dir(), 'images')
    disk_dict = {'source': {'attrs': {'file': os.path.join(default_image_path,
                 os.path.basename(blk_source))}}}
    libvirt_vmxml.modify_vm_device(
            vm_xml.VMXML.new_from_inactive_dumpxml(vm_name),
            'disk', disk_dict)


def prepare_disk_remote(params, blk_source):
    """
    Prepare disk on remote host

    :param params: dictionary with the test parameter
    :param blk_source: first disk path
    """
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")

    remote_session = remote.remote_login("ssh", server_ip, "22",
                                         server_user, server_pwd,
                                         r'[$#%]')

    image_info = utils_misc.get_image_info(blk_source)
    disk_size = image_info.get("vsize")
    disk_format = image_info.get("format")
    utils_misc.make_dirs(os.path.dirname(blk_source), remote_session)
    libvirt_disk.create_disk("file", path=blk_source,
                             size=disk_size, disk_format=disk_format,
                             session=remote_session)
    remote_session.close()


def prepare_disks(params, blk_source, vm_name):
    """
    Prepare disk on local and remote host

    :param params: dictionary with the test parameter
    :param blk_source: first disk path
    :param vm_name: vm name
    """
    disk2_dict = eval(params.get("disk2_dict"))
    disk2_name = params.get("disk2_name")
    disk_format = params.get("disk_format", "qcow2")

    disk2_path = os.path.join(os.path.dirname(blk_source), disk2_name)
    update_vm_with_additional_disk(disk2_dict, disk_format, vm_name, disk2_path)

    prepare_disk_remote(params, blk_source)
    prepare_disk_remote(params, disk2_path)


def run(test, params, env):
    """
    To verify that libvirt can specifies which disks to migrate during
    migration.

    :param test: test object
    :param params: dictionary with the test parameters
    :param env: dictionary with test environment.
    """
    def setup_common():
        """
        Common setup steps

        """
        test.log.info("Common setup steps.")
        migration_obj.setup_connection()
        prepare_disks(params, blk_source, vm_name)
        vm.start()
        vm.wait_for_login().close()

    def setup_disk1_disk2_disk3():
        """
        Setup steps for disk1_disk2_disk3 case

        """
        disk3_dict = eval(params.get("disk3_dict"))
        disk3_name = params.get("disk3_name")
        nfs_mount_dir = params.get("nfs_mount_dir")
        migrate_desturi_port = params.get("migrate_desturi_port")
        migrate_desturi_type = params.get("migrate_desturi_type", "tcp")

        migration_obj.conn_list.append(migration_base.setup_conn_obj(migrate_desturi_type, params, test))
        migration_obj.remote_add_or_remove_port(migrate_desturi_port)

        test.log.info("Setup steps for disk1_disk2_disk3 case.")
        prepare_disk_local(blk_source, vm_name)
        prepare_disks(params, blk_source, vm_name)

        disk3_path = os.path.join(nfs_mount_dir, disk3_name)
        update_vm_with_additional_disk(disk3_dict, disk_format, vm_name, disk3_path)

        vm.start()
        vm.wait_for_login().close()

    def setup_disk1_disk2_disk4():
        """
        Setup steps for disk1_disk2_disk4 case

        """
        disk4_dict = eval(params.get("disk4_dict"))
        disk4_name = params.get("disk4_name")
        nfs_mount_dir = params.get("nfs_mount_dir")
        migrate_desturi_port = params.get("migrate_desturi_port")
        migrate_desturi_type = params.get("migrate_desturi_type", "tcp")

        migration_obj.conn_list.append(migration_base.setup_conn_obj(migrate_desturi_type, params, test))
        migration_obj.remote_add_or_remove_port(migrate_desturi_port)

        test.log.info("Setup steps for disk1_disk2_disk4 case.")
        prepare_disk_local(blk_source, vm_name)
        prepare_disks(params, blk_source, vm_name)

        disk4_path = os.path.join(nfs_mount_dir, disk4_name)
        update_vm_with_additional_disk(disk4_dict, disk_format, vm_name, disk4_path)

        vm.start()
        vm.wait_for_login().close()

    def setup_disk1_disk2_disk5():
        """
        Setup steps for disk1_disk2_disk5 case

        """
        disk5_dict = eval(params.get("disk5_dict"))

        test.log.info("Setup steps for disk1_disk2_disk5 case.")
        migration_obj.setup_connection()
        prepare_disks(params, blk_source, vm_name)
        update_vm_with_additional_disk(disk5_dict, disk_format, vm_name)

        vm.start()
        vm.wait_for_login().close()

    disks = params.get("disks")
    vm_name = params.get("migrate_main_vm")
    disk_format = params.get("disk_format", "qcow2")

    vm = env.get_vm(vm_name)
    blk_source = vm.get_first_disk_devices()['source']
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % disks) if "setup_%s" % disks in \
        locals() else setup_common

    try:
        setup_test()
        migration_obj.run_migration()
        migration_obj.verify_default()
    finally:
        migration_obj.cleanup_connection()
        base_steps.cleanup_disks_remote(params, vm)
