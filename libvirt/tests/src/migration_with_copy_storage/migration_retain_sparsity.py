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

from virttest import ceph
from virttest import data_dir
from virttest import remote
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.libvirt_xml.devices import disk

from provider.migration import base_steps


def prepare_disks_local(params, blk_source, vm_name):
    """
    Prepare disks on local host

    :param blk_source: first disk path
    :param vm_name: vm name
    """
    src_disk1_dict = eval(params.get("src_disk1_dict"))
    src_disk2_dict = params.get("src_disk2_dict")
    src_disk1_backend = params.get("src_disk1_backend")
    src_disk2_backend = params.get("src_disk2_backend")
    disk1_name = params.get("disk1_name")
    disk2_name = params.get("disk2_name")
    disk_size = params.get("disk_size")
    mon_host = params.get("mon_host")

    if src_disk1_backend == "rbd":
        ceph.rbd_image_rm(mon_host, "pool", disk1_name)
        ceph.rbd_image_create(mon_host, "pool", disk1_name, disk_size)
        disk1_path = ceph.rbd_image_map(mon_host, "pool", disk1_name)
        src_disk1_dict.update({'source': {'attrs': {'file': disk1_path}}})
    else:
        disk1_path = os.path.join(os.path.dirname(blk_source), disk1_name)
        if os.path.exists(disk1_path):
            os.remove(disk1_path)
        libvirt_disk.create_disk("file", size=disk_size, disk_format=src_disk1_backend, path=disk1_path)
        src_disk1_dict.update({'source': {'attrs': {'file': disk1_path}}})

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", src_disk1_dict))
    vmxml.sync()

    if src_disk2_backend:
        src_disk2_dict = eval(src_disk2_dict)
        disk2_path = os.path.join(os.path.dirname(blk_source), disk2_name)
        if os.path.exists(disk2_path):
            os.remove(disk2_path)
        libvirt_disk.create_disk("file", size=disk_size, disk_format=src_disk2_backend, path=disk2_path)
        src_disk2_dict.update({'source': {'attrs': {'file': disk2_path}}})
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", src_disk2_dict))
        vmxml.sync()


def prepare_disks_remote(params, blk_source):
    """
    Prepare disks on remote host

    :param params: dictionary with the test parameter
    :param blk_source: first disk path
    """
    dest_disk1_backend = params.get("dest_disk1_backend")
    dest_disk2_backend = params.get("dest_disk2_backend")
    disk1_name = params.get("disk1_name")
    disk2_name = params.get("disk2_name")
    disk_size = params.get("disk_size")
    mon_host = params.get("mon_host")
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")

    remote_session = remote.remote_login("ssh", server_ip, "22",
                                         server_user, server_pwd,
                                         r'[$#%]')
    if dest_disk1_backend == "rbd":
        cmd1 = f"rbd create pool/{disk1_name} --size={disk_size}"
        remote.run_remote_cmd(cmd1, params)
        cmd2 = f"rbd device map --pool pool {disk1_name}"
        remote.run_remote_cmd(cmd2, params)
    else:
        disk_path = os.path.join(os.path.dirname(blk_source), disk1_name)
        utils_misc.make_dirs(os.path.dirname(blk_source), remote_session)
        libvirt_disk.create_disk("file", path=disk_path,
                                 size=disk_size, disk_format=dest_disk1_backend,
                                 session=remote_session)

    if dest_disk2_backend:
        disk2_path = os.path.join(os.path.dirname(blk_source), disk2_name)
        libvirt_disk.create_disk("file", path=disk2_path,
                                 size=disk_size, disk_format=dest_disk2_backend,
                                 session=remote_session)
    remote_session.close()


def prepare_in_vm(vm, params):
    """
    Prepare in vm

    :param vm: vm object
    :param params: dictionary with the test parameters
    """
    dest_disk1_backend = params.get("dest_disk1_backend")

    vm_session = vm.wait_for_serial_login(timeout=120)
    vm_session.cmd("dd if=/dev/zero of=/dev/sdb bs=512 count=2097152")
    vm.session.cmd("mkfs.xfs /dev/sdb")
    vm.session.cmd("dd if=/dev/random of=/media/data bs=1048576 count=100")

    # check current disk usage
    if dest_disk1_backend == "rbd":
        process.run("rbd du --pool=pool", shell=True)
    else:
        out = vm_session.cmd("df -h | grep 'sdb'")
    vm_session.close()


def prepare_migratable_xml(vm_name, params, test):
    """
    Prepare migratable xml

    :param vm_name: vm name
    :param params: dictionary with the test parameters
    """
    dest_disk1_backend = params.get("dest_disk1_backend")
    dest_disk2_backend = params.get("dest_disk2_backend")
    dest_disk1_dict = eval(params.get("dest_disk1_dict"))
    dest_disk2_dict = params.get("dest_disk2_dict")

    mig_disk1 = disk.Disk(type_name=dest_disk1_backend)
    mig_disk1.setup_attrs(**dest_disk1_dict)
    if dest_disk2_backend:
        dest_disk2_dict = eval(dest_disk2_dict)
        mig_disk2 = disk.Disk(type_name=dest_disk2_backend)
        mig_disk2.setup_attrs(**dest_disk2_dict)

    guest_xml = vm_xml.VMXML.new_from_dumpxml(vm_name, options="--migratable")
    guest_xml.add_device(mig_disk1)
    if dest_disk2_backend:
        guest_xml.add_device(mig_disk2)
    guest_xml.xmltreefile.write()
    tmp_dir = data_dir.get_tmp_dir()
    xmlfile = os.path.join(tmp_dir, "xml_file")
    shutil.copyfile(guest_xml.xml, xmlfile)
    params.update({"virsh_migrate_extra": "--xml %s" % xmlfile})


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
        test.log.info("Setup steps.")
        migration_obj.setup_connection()
        blk_source = vm.get_first_disk_devices()['source']
        prepare_migratable_xml(vm_name, params, test)
        prepare_disks_local(params, blk_source, vm_name)
        prepare_disks_remote(params, blk_source)
        vm.start()
        prepare_in_vm(vm, params)

    def verify_test():
        """
        Verify steps

        """
        dest_disk1_backend = params.get("dest_disk1_backend")
        disk1_name = params.get("disk1_name")
        disk_size = params.get("disk_size")

        test.log.info("Verify steps.")
        migration_obj.verify_default()
        if dest_disk1_backend == "rbd":
            cmd = "rbd du --pool=pool"
        else:
            blk_source = vm.get_first_disk_devices()['source']
            disk_path = os.path.join(os.path.dirname(blk_source), disk1_name)
            image_info = utils_misc.get_image_info(disk_path)
            if image_info["dsize"] == disk_size:
                test.fail(f"Disk usage should not be {disk_size}.")

    def cleanup_test():
        """
        Cleanup steps

        """
        src_disk1_backend = params.get("src_disk1_backend")
        src_disk2_backend = params.get("src_disk2_backend")
        dest_disk1_backend = params.get("dest_disk1_backend")
        dest_disk2_backend = params.get("dest_disk2_backend")
        disk1_name = params.get("disk1_name")
        disk2_name = params.get("disk2_name")

        test.log.info("Cleanup steps.")
        migration_obj.cleanup_connection()
        blk_source = vm.get_first_disk_devices()['source']
        if src_disk1_backend == "rbd":
            test.log.info("Add steps to remove rbd on src.")
        else:
            disk1_path = os.path.join(os.path.dirname(blk_source), disk1_name)
            if os.path.exists(disk1_path):
                os.remove(disk1_path)
        if src_disk2_backend:
            disk2_path = os.path.join(os.path.dirname(blk_source), disk2_name)
            if os.path.exists(disk2_path):
                os.remove(disk2_path)

        if dest_disk1_backend == "rbd":
            test.log.info("Add steps to remove rbd.")
        else:
            disk1_path = os.path.join(os.path.dirname(blk_source), disk1_name)
            cmd = f"rm -f {disk1_path}"
            remote.run_remote_cmd(cmd, params, ignore_status=False)
        if dest_disk2_backend:
            disk2_path = os.path.join(os.path.dirname(blk_source), disk2_name)
            cmd = f"rm -f {disk2_path}"
            remote.run_remote_cmd(cmd, params, ignore_status=False)

    vm_name = params.get("migrate_main_vm")

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        cleanup_test()
