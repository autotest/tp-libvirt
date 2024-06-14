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
import shutil

from avocado.utils import process

from virttest import data_dir
from virttest import remote
from virttest import utils_misc
from virttest import utils_test
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import disk
from virttest.staging import lv_utils
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps
from provider.migration import migration_base

dev_target = None


def setup_disk_on_source(vm, params, test):
    """
    Setup disk on source host

    :param vm: VM object
    :param params: dictionary with the test parameters
    :param test: test object
    """
    disk_size = params.get("disk_size")
    disk_format = params.get("disk_format")
    src_iscsi_size = params.get("src_iscsi_size")
    src_lv_name = params.get("src_lv_name")
    src_vg_name = params.get("src_vg_name")
    disk_type = params.get("disk_type")
    disk_source_name = params.get("disk_source_name")
    src_block_path = params.get("src_block_path")
    client_ip = params.get("client_ip")
    slice_offset = params.get("slice_offset")
    slice_size = params.get("slice_size")

    test.log.info("Prepare disk on source.")
    blk_source = vm.get_first_disk_devices()['source']
    if disk_type == "block":
        dev_src = libvirt.setup_or_cleanup_iscsi(is_setup=True, is_login=True, image_size=src_iscsi_size, emulated_image="emulated-iscsi1", portal_ip=client_ip)
        if not lv_utils.vg_check(src_vg_name):
            lv_utils.vg_create(src_vg_name, dev_src)
        if not lv_utils.lv_check(src_vg_name, src_lv_name):
            lv_utils.lv_create(src_vg_name, src_lv_name, disk_size)
        cmd = "qemu-img convert -f qcow2 -O %s %s %s" % (disk_format, blk_source, src_block_path)
    else:
        cmd = "qemu-img convert -f qcow2 -O %s %s %s" % (disk_format, blk_source, disk_source_name)
    process.run(cmd, shell=True)


def setup_migratable_xml(vm_name, params):
    """
    Set migratable xml

    :param vm_name: vm name
    :param params: dictionary with the test parameters
    """
    target_disk_type = params.get("target_disk_type")
    target_disk_dict = eval(params.get("target_disk_dict"))

    mig_disk = disk.Disk(type_name=target_disk_type)
    mig_disk.setup_attrs(**target_disk_dict)

    guest_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, options="--migratable")
    guest_xml.remove_all_device_by_type("disk")
    guest_xml.add_device(mig_disk)
    guest_xml.xmltreefile.write()
    tmp_dir = data_dir.get_tmp_dir()
    xmlfile = os.path.join(tmp_dir, "xml_file")
    shutil.copyfile(guest_xml.xml, xmlfile)
    params.update({"virsh_migrate_extra": "--xml %s" % xmlfile})


def setup_disk_on_target(params, test, block_dev):
    """
    Setup disk on target host

    :param params: dictionary with the test parameters
    :param test: test object
    :param block_dev: remote disk manager object
    """
    disk_format = params.get("disk_format")
    target_iscsi_size = params.get("target_iscsi_size")
    target_lv_name = params.get("target_lv_name")
    target_vg_name = params.get("target_vg_name")
    target_disk_size = params.get("target_disk_size")
    target_disk_type = params.get("target_disk_type")
    disk_source_name = params.get("disk_source_name")
    target_block_path = params.get("target_block_path")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    client_ip = params.get("client_ip")

    test.log.info("Prepare disk on target.")
    if target_disk_type == "block":
        dev_target, _ = libvirt.setup_or_cleanup_iscsi(is_setup=True, is_login=False, image_size=target_iscsi_size, emulated_image="emulated-iscsi2", portal_ip=client_ip)
        remote_dev = block_dev.iscsi_login_setup(client_ip, dev_target)
        block_dev.create_vg(target_vg_name, remote_dev)
        block_dev.create_image("lvm", size=target_disk_size, vgname=target_vg_name, lvname=target_lv_name, sparse=False, timeout=60)
    else:
        test.log.info("Prepare a raw format image on target.")
        remote_session = remote.remote_login("ssh", server_ip, "22",
                                             server_user, server_pwd,
                                             r'[$#%]')
        utils_misc.make_dirs(os.path.dirname(disk_source_name), remote_session)
        libvirt_disk.create_disk("file", path=disk_source_name,
                                 size=target_disk_size, disk_format=disk_format,
                                 session=remote_session)
        remote_session.close()


def cleanup_disk_on_source(params, test):
    """
    Cleanup disk on source host

    :param params: dictionary with the test parameters
    :param test: test object
    """
    src_lv_name = params.get("src_lv_name")
    src_vg_name = params.get("src_vg_name")
    disk_type = params.get("disk_type")
    disk_source_name = params.get("disk_source_name")
    client_ip = params.get("client_ip")

    test.log.info("Cleanup disk on source.")
    if disk_type == "block":
        if lv_utils.lv_check(src_vg_name, src_lv_name):
            lv_utils.lv_remove(src_vg_name, src_lv_name)
        if lv_utils.vg_check(src_vg_name):
            lv_utils.vg_remove(src_vg_name)
        libvirt.setup_or_cleanup_iscsi(is_setup=False, emulated_image="emulated-iscsi1", portal_ip=client_ip)
        cmd = "dmsetup ls"
        ret = process.run(cmd, shell=True).stdout_text.strip()
        dev_name = "%s-%s" % (src_vg_name, src_lv_name)
        if dev_name in ret:
            cmd = "dmsetup remove %s" % dev_name
            process.run(cmd, shell=True)
    else:
        cmd = "rm -rf %s" % disk_source_name
        process.run(cmd, shell=True)


def cleanup_disk_on_target(params, test, block_dev):
    """
    Cleanup disk on target host

    :param params: dictionary with the test parameters
    :param test: test object
    :param block_dev: remote disk manager object
    """
    target_lv_name = params.get("target_lv_name")
    target_vg_name = params.get("target_vg_name")
    target_disk_type = params.get("target_disk_type")
    disk_source_name = params.get("disk_source_name")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user", "root")
    server_pwd = params.get("server_pwd")
    client_ip = params.get("client_ip")

    test.log.info("Cleanup disk on target.")
    run_on_target = remote.RemoteRunner(host=server_ip, username=server_user, password=server_pwd)
    if target_disk_type == "block":
        block_dev.remove_path("lvm", "/dev/%s/%s" % (target_vg_name, target_lv_name))
        block_dev.remove_vg(target_vg_name)
        block_dev.iscsi_login_setup(client_ip, dev_target, is_login=False)
        libvirt.setup_or_cleanup_iscsi(is_setup=False, emulated_image="emulated-iscsi2", portal_ip=client_ip)
        cmd = "dmsetup ls"
        ret = remote.run_remote_cmd(cmd, params, run_on_target, ignore_status=False).stdout_text.strip()
        dev_name = "%s-%s" % (target_vg_name, target_lv_name)
        if dev_name in ret:
            cmd = "dmsetup remove %s" % dev_name
            remote.run_remote_cmd(cmd, params, run_on_target, ignore_status=False)
    else:
        cmd = "rm -rf %s" % disk_source_name
        remote.run_remote_cmd(cmd, params, run_on_target, ignore_status=False)


def run(test, params, env):
    """
    To verify that live migration with copying storage when target image size
    is larger than source image size.

    :param test: test object
    :param params: dictionary with the test parameters
    :param env: dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        migrate_desturi_port = params.get("migrate_desturi_port")
        migrate_desturi_type = params.get("migrate_desturi_type", "tcp")
        src_disk_dict = eval(params.get("src_disk_dict"))

        test.log.info("Setup steps.")
        migration_obj.conn_list.append(migration_base.setup_conn_obj(migrate_desturi_type, params, test))
        migration_obj.remote_add_or_remove_port(migrate_desturi_port)

        setup_disk_on_source(vm, params, test)

        new_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        new_xml.remove_all_device_by_type('disk')
        libvirt_vmxml.modify_vm_device(new_xml, 'disk', src_disk_dict)

        vm.start()
        vm.wait_for_login().close()

        setup_disk_on_target(params, test, block_dev)
        setup_migratable_xml(vm_name, params)

    def verify_test():
        """
        Verify steps

        """
        test.log.info("Verify steps.")
        target_disk_size = params.get("target_disk_size")
        desturi = params.get("virsh_migrate_desturi")

        virsh.blockresize(vm_name, "vda", target_disk_size, ignore_status=False, uri=desturi)
        migration_obj.verify_default()

    def cleanup_test():
        """
        Cleanup steps

        """
        test.log.info("Cleanup steps.")
        srcuri = params.get("virsh_migrate_connect_uri")
        desturi = params.get("virsh_migrate_desturi")

        vm.connect_uri = desturi
        if vm.is_alive():
            vm.destroy()
        vm.connect_uri = srcuri

        cleanup_disk_on_target(params, test, block_dev)
        cleanup_disk_on_source(params, test)

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    block_dev = utils_test.RemoteDiskManager(params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        cleanup_test()
        migration_obj.cleanup_connection()
