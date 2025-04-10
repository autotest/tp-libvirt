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

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.libvirt_xml.devices import disk

from provider.migration import base_steps


def prepare_disks_local(params, vm_name):
    """
    Prepare disks on local host

    :param params: dictionary with the test parameter
    :param vm_name: vm name
    :return: rbd device name on source host
    """
    src_disk1_dict = eval(params.get("src_disk1_dict"))
    src_driver_type_1 = params.get("src_driver_type_1")
    src_disk1_name = params.get("src_disk1_name")
    disk_size = params.get("disk_size")
    disk_path = params.get("disk_path")
    disk2_name = params.get("disk2_name")
    src_rbd_dev = None

    def _prepare_disk_xml(disk_name, disk_format, disk_dict):
        """
        Prepare disk xml

        :param disk_name: disk name
        :param disk_format: disk format
        :param disk_dict: disk dict
        """
        disk_file = os.path.join(disk_path, disk_name)
        if os.path.exists(disk_file):
            os.remove(disk_file)
        libvirt_disk.create_disk("file", size=disk_size, disk_format=disk_format, path=disk_file)
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", disk_dict))
        vmxml.sync()

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    if src_driver_type_1 == "rbd":
        ceph_pool_name = params.get("ceph_pool_name")
        cmd1 = f"rbd create {ceph_pool_name}/{src_disk1_name} --size={disk_size}"
        process.run(cmd1, shell=True, verbose=True)
        cmd2 = f"rbd device map --pool {ceph_pool_name} {src_disk1_name}"
        src_rbd_dev = process.run(cmd2, shell=True, verbose=True).stdout_text.strip()
        src_disk1_dict.update({'source': {'attrs': {'dev': src_rbd_dev}}})
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", src_disk1_dict))
        vmxml.sync()
    else:
        _prepare_disk_xml(src_disk1_name, src_driver_type_1, src_disk1_dict)

    if disk2_name:
        _prepare_disk_xml(disk2_name, params.get("src_driver_type_2"), eval(params.get("src_disk2_dict")))
    return src_rbd_dev


def prepare_disks_remote(params):
    """
    Prepare disks on remote host

    :param params: dictionary with the test parameter
    :return: rbd device name on target host and updated disk dict
    """
    dest_disk1_dict = eval(params.get("dest_disk1_dict"))
    dest_driver_type_1 = params.get("dest_driver_type_1")
    dest_disk1_name = params.get("dest_disk1_name")
    disk_size = params.get("disk_size")
    disk_path = params.get("disk_path")
    disk2_name = params.get("disk2_name")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")
    dest_rbd_dev = None

    remote_session = remote.remote_login("ssh", server_ip, "22",
                                         server_user, server_pwd,
                                         r'[$#%]')
    if dest_driver_type_1 == "rbd":
        ceph_pool_name = params.get("ceph_pool_name")
        cmd1 = f"rbd create {ceph_pool_name}/{dest_disk1_name} --size={disk_size}"
        remote.run_remote_cmd(cmd1, params)
        cmd2 = f"rbd device map --pool {ceph_pool_name} {dest_disk1_name}"
        dest_rbd_dev = remote.run_remote_cmd(cmd2, params).stdout_text.strip()
        dest_disk1_dict.update({'source': {'attrs': {'dev': dest_rbd_dev}}})
    else:
        disk_file = os.path.join(disk_path, dest_disk1_name)
        utils_misc.make_dirs(disk_path, remote_session)
        libvirt_disk.create_disk("file", path=disk_file,
                                 size=disk_size, disk_format=dest_driver_type_1,
                                 session=remote_session)

    if disk2_name:
        disk2_file = os.path.join(disk_path, disk2_name)
        libvirt_disk.create_disk("file", path=disk2_file,
                                 size=disk_size, disk_format=params.get("dest_driver_type_2"),
                                 session=remote_session)
    remote_session.close()
    return dest_rbd_dev, dest_disk1_dict


def prepare_migratable_xml(vm_name, device_type, disk1_dict, test, disk2_dict=None):
    """
    Prepare migratable xml

    :param vm_name: vm name
    :param device_type: device type
    :param disk1_dict: disk1 dict
    :param test: test object
    :param disk2_dict: disk2 dict
    """
    test.log.debug("Prepare migratable xml.")
    mig_disk1 = disk.Disk(type_name=device_type)
    mig_disk1.setup_attrs(**disk1_dict)
    if disk2_dict:
        mig_disk2 = disk.Disk(type_name="file")
        mig_disk2.setup_attrs(**eval(disk2_dict))

    guest_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, options="--migratable")
    guest_xml.add_device(mig_disk1)
    if disk2_dict:
        guest_xml.add_device(mig_disk2)
    guest_xml.xmltreefile.write()
    tmp_dir = data_dir.get_tmp_dir()
    xmlfile = os.path.join(tmp_dir, "xml_file")
    shutil.copyfile(guest_xml.xml, xmlfile)
    test.log.debug(f"migratable xml: {xmlfile}")
    return xmlfile


def get_disk_size(disk_file, remote_host=False, params=False):
    """
    Get disk size

    :param disk_file: disk file
    :param remote_host: if true, get disk size on target host
    :param params: dictionary with the test parameters
    :return: disk size
    """
    cmd = f"qemu-img info {disk_file} -U"
    if remote_host:
        ret = remote.run_remote_cmd(cmd, params).stdout_text.strip()
    else:
        ret = process.run(cmd, shell=True).stdout_text.strip()
    return ret.split('\n')[3].split(':')[1].strip()


def run(test, params, env):
    """
    To verify that live migration with copying storage can retain sparsity with
    option --migrate-disks-detect-zeroes.

    :param test: test object
    :param params: dictionary with the test parameters
    :param env: dictionary with test environment.
    """
    def setup_test():
        """
        Setup steps

        """
        src_disk1_name = params.get("src_disk1_name")
        dest_disk1_dict = eval(params.get("dest_disk1_dict"))
        ceph_pool_name = params.get("ceph_pool_name")
        target_dev_1 = params.get("target_dev_1")
        target_dev_2 = params.get("target_dev_2")
        disk2_name = params.get("disk2_name")

        test.log.info("Setup steps.")
        migration_obj.setup_connection()
        base_steps.prepare_disks_remote(params, vm)

        nonlocal dest_rbd_dev
        dest_rbd_dev, dest_disk1_dict = prepare_disks_remote(params)
        test.log.debug(f"dest_disk1_dict: {dest_disk1_dict}")
        test.log.debug(f"dest_rbd_dev: {dest_rbd_dev}")
        xmlfile = prepare_migratable_xml(vm_name, params.get("dest_device_type_1"),
                                         dest_disk1_dict, test,
                                         disk2_dict=params.get("dest_disk2_dict"))
        params.update({"virsh_migrate_extra": "--xml %s" % xmlfile})

        nonlocal src_rbd_dev
        src_rbd_dev = prepare_disks_local(params, vm_name)
        test.log.debug(f"src_rbd_dev: {src_rbd_dev}")

        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=120)
        vm_session.cmd(f"mkfs.xfs /dev/{target_dev_1}", timeout=600)
        vm_session.cmd(f"dd if=/dev/random of=/dev/{target_dev_1} bs=1048576 count={params.get('dd_count')}")
        if disk2_name:
            vm_session.cmd(f"mkfs.xfs /dev/{target_dev_2}", timeout=100)
            vm_session.cmd(f"dd if=/dev/random of=/dev/{target_dev_2} bs=1048576 count={params.get('dd_count')}")
        vm_session.close()

        nonlocal old_disk_size
        if params.get("src_driver_type_1") == "rbd":
            cmd = f"rbd du --pool={ceph_pool_name} | grep {src_disk1_name}"
            old_disk_size.append(process.run(cmd, shell=True).stdout_text.strip().split("  ")[-1])
        else:
            old_disk_size.append(get_disk_size(os.path.join(disk_path, src_disk1_name)))
        if disk2_name:
            old_disk_size.append(get_disk_size(os.path.join(disk_path, disk2_name)))
        test.log.debug(f"old disk size: {old_disk_size}")

    def verify_test():
        """
        Verify steps

        """
        dest_disk1_name = params.get("dest_disk1_name")
        disk2_name = params.get("disk2_name")
        ceph_pool_name = params.get("ceph_pool_name")
        disk_size = params.get("disk_size")

        test.log.info("Verify steps.")
        if params.get("dest_driver_type_1") == "rbd":
            cmd = f"rbd du --pool={ceph_pool_name} | grep {dest_disk1_name}"
            new_disk_size = remote.run_remote_cmd(cmd, params).stdout_text.strip().split('  ')[-1].split(' ')
        else:
            new_disk_size = get_disk_size(os.path.join(disk_path, dest_disk1_name), remote_host=True, params=params).split(' ')
        if new_disk_size[1][0] == disk_size[-1]:
            if float(new_disk_size[0]) == float(disk_size[:-1]):
                test.fail(f"Disk1 usage should not be {disk_size}: before migrate: {old_disk_size[0]}, after migrate: {new_disk_size}")
        if disk2_name:
            disk2_size = get_disk_size(os.path.join(disk_path, disk2_name), remote_host=True, params=params).split(' ')
            if disk2_size[1][0] == disk_size[-1]:
                if float(disk2_size[0]) == float(disk_size[:-1]):
                    test.fail(f"Disk2 usage should not be {disk_size}: before migrate: {old_disk_size[1]}, after migrate: {disk2_size}")
        migration_obj.verify_default()

    def cleanup_test():
        """
        Cleanup steps

        """
        src_disk1_name = params.get("src_disk1_name")
        dest_disk1_name = params.get("dest_disk1_name")
        disk2_name = params.get("disk2_name")
        ceph_pool_name = params.get("ceph_pool_name")

        def _remove_disk_local(disk_name):
            """
            Remove disk on local

            :param disk_name: disk name
            """
            disk_file = os.path.join(disk_path, disk_name)
            if os.path.exists(disk_file):
                os.remove(disk_file)

        def _remove_disk_remote(disk_name):
            """
            Remove disk on remote

            :param disk_name: disk name
            """
            disk_file = os.path.join(disk_path, disk_name)
            remote.run_remote_cmd(f"rm -f {disk_file}", params, ignore_status=False)

        test.log.info("Cleanup steps.")
        migration_obj.cleanup_connection()
        if params.get("dest_driver_type_1") == "rbd":
            cmd = f"rbd unmap {dest_rbd_dev}"
            remote.run_remote_cmd(cmd, params)
            cmd2 = f"rbd remove --pool {ceph_pool_name} {dest_disk1_name}"
            remote.run_remote_cmd(cmd2, params)
        else:
            _remove_disk_remote(dest_disk1_name)
        if disk2_name:
            _remove_disk_remote(disk2_name)

        if params.get("src_driver_type_1") == "rbd":
            cmd = f"rbd unmap {src_rbd_dev}"
            process.run(cmd, shell=True, verbose=True)
            cmd2 = f"rbd remove --pool {ceph_pool_name} {src_disk1_name}"
            process.run(cmd2, shell=True, verbose=True)
        else:
            _remove_disk_local(src_disk1_name)
        if disk2_name:
            _remove_disk_local(disk2_name)
        base_steps.cleanup_disks_remote(params, vm)

    vm_name = params.get("migrate_main_vm")
    disk_path = params.get("disk_path")
    old_disk_size = []
    src_rbd_dev = None
    dest_rbd_dev = None

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        cleanup_test()
