import math
import os
import time

from virttest import virsh
from virttest import remote
from virttest import utils_disk
from virttest import utils_misc

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.migration import base_steps


def prepare_disks_local(disk_list, disk_format="qcow2"):
    """
    Prepare disk image on source host

    :param disk_list: list, the list of disk image
    :param disk_format: disk format
    """
    for disk_img in disk_list:
        if os.path.exists(disk_img):
            os.remove(disk_img)
        libvirt_disk.create_disk("file", disk_format=disk_format, path=disk_img)


def prepare_disks_remote(params, disk_list, disk_format="qcow2", disk_size="500M", extra=''):
    """
    Prepare disk image on target host

    :param params: dictionary with the test parameter
    :param disk_list: list, the list of disk images
    :param disk_format: disk format
    :param disk_size: disk size
    :param extra: extra parameters
    """
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")

    # prepare disks on remote
    remote_session = remote.remote_login("ssh", server_ip, "22",
                                         server_user, server_pwd,
                                         r'[$#%]')
    for disk_img in disk_list:
        libvirt_disk.create_disk("file", disk_format=disk_format,
                                 path=disk_img, size=disk_size,
                                 session=remote_session,
                                 extra=extra)
    remote_session.close()


def prepare_backingchain(base_img, top_img, disk_format="qcow2", disk_size="1G"):
    """
    Prepare backingchain for image

    :param base_img: base image path
    :param top_img: top image path
    :param disk_format: disk format
    :param disk_size: disk size
    """
    for img in [base_img, top_img]:
        if os.path.exists(img):
            os.remove(img)
    libvirt_disk.create_disk("file", disk_format=disk_format, path=base_img, size=disk_size)
    libvirt_disk.create_disk("file", disk_format=disk_format, path=top_img,
                             size=disk_size,
                             extra="-F %s -b %s" % (disk_format, base_img))


def copy_img_to_remote(params, disk_list):
    """
    Copy disk image from source host to target host

    :param params: dictionary with the test parameter
    :param disk_list: list, the list of disk images
    """
    server_ip = params.get("server_ip")
    server_user = params.get("server_user")
    server_pwd = params.get("server_pwd")

    for disk_img in disk_list:
        remote.copy_files_to(server_ip, 'scp', server_user, server_pwd,
                             '22', disk_img, disk_img)


def get_remote_disk_info(params, disk_img, test):
    """
    Get disk image information from target host

    :param params: dictionary with the test parameter
    :param test: test object
    :param disk_img: disk image path
    :return: dict, return disk image information
    """
    cmd = "qemu-img info %s -U" % disk_img
    ret = remote.run_remote_cmd(cmd, params, ignore_status=False)
    if ret.exit_status:
        test.fail("Failed to get disk info on remote.")
    disk_info = ret.stdout_text.strip()
    disk_info_dict = {}
    vsize = None
    if disk_info:
        for line in disk_info.splitlines():
            if line.find("virtual size") != -1 and vsize is None:
                vsize = line.split("(")[-1].strip().split(" ")[0]
                disk_info_dict['vsize'] = int(vsize)
            elif line.find("disk size") != -1:
                dsize = line.split(':')[-1].strip()
                disk_info_dict['dsize'] = int(float(
                    utils_misc.normalize_data_size(dsize, order_magnitude="B",
                                                   factor=1024)))
    return disk_info_dict


def check_disk_info(params, old_disk_info, disk_img, test, disk_diff_rate=None):
    """
    Check disk information

    :param params: Dictionary with the test parameter
    :param old_disk_info: disk information on source host
    :param disk_img: disk image path
    :param test: test object
    :param disk_diff_rate: the different rate for disk image
    """
    new_disk_info = get_remote_disk_info(params, disk_img, test)
    if old_disk_info['vsize'] != new_disk_info['vsize']:
        test.fail("Check virtual size failed: old: %s, new: %s." % (old_disk_info['vsize'], new_disk_info['vsize']))
    if disk_diff_rate:
        if (math.fabs(float(new_disk_info['dsize']) - float(old_disk_info['dsize'])) //
                float(old_disk_info['dsize']) > disk_diff_rate):
            test.fail("Check disk size failed: old: %s, new: %s." % (old_disk_info['dsize'], new_disk_info['dsize']))
    else:
        if old_disk_info['dsize'] != new_disk_info['dsize']:
            test.fail("Check disk size failed: old: %s, new: %s." % (old_disk_info['dsize'], new_disk_info['dsize']))


def check_disk(params, vm):
    """
    Check disk read/write

    :param params: Dictionary with the test parameter
    :param vm: vm object
    """
    dest_uri = params.get("virsh_migrate_desturi")
    disk_target1 = params.get("disk_target1")
    disk_target2 = params.get("disk_target2")

    vm.cleanup_serial_console()
    backup_uri, vm.connect_uri = vm.connect_uri, dest_uri
    vm.create_serial_console()
    remote_vm_session = vm.wait_for_serial_login(timeout=120)
    utils_disk.linux_disk_check(remote_vm_session, disk_target1)
    utils_disk.linux_disk_check(remote_vm_session, disk_target2)

    remote_vm_session.cmd("reboot", ignore_all_errors=True)
    remote_vm_session.close()
    remote_vm_session = vm.wait_for_serial_login(timeout=120)
    utils_disk.linux_disk_check(remote_vm_session, disk_target1)
    utils_disk.linux_disk_check(remote_vm_session, disk_target2)
    remote_vm_session.close()
    vm.cleanup_serial_console()
    vm.connect_uri = backup_uri


def run(test, params, env):
    """
    Test VM live migration with copy storage - disk with/without backing chain (basic function).

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def setup_with_backing_chain():
        """
        Setup step for migration with backing chain case
        """
        virsh_migrate_extra = params.get("virsh_migrate_extra")
        top_img = params.get("top_img")
        base1_img = params.get("base1_img")
        top1_img = params.get("top1_img")
        base2_img = params.get("base2_img")
        top2_img = params.get("top2_img")
        disk_target1 = params.get("disk_target1")
        disk_target2 = params.get("disk_target2")
        disk_format = params.get("disk_format", "qcow2")
        disk_size = params.get("disk_size", "1G")
        dest_uri = params.get("virsh_migrate_desturi")
        pool_name = params.get("target_pool_name")
        pool_type = params.get("target_pool_type")
        pool_target = params.get("target_pool_target")
        disk1_dict = eval(params.get("disk1_dict"))
        disk2_dict = eval(params.get("disk2_dict"))
        disk3_dict = eval(params.get("disk3_dict"))

        test.log.info("Setup for migration with backing chain.")
        migration_obj.setup_connection()

        prepare_backingchain(base1_img, top1_img, disk_size=disk_size)
        prepare_backingchain(base2_img, top2_img, disk_size=disk_size)

        source_file = vm.get_first_disk_devices()['source']
        source_size = utils_misc.get_image_info(source_file).get("vsize")
        source_size = str(int(int(source_size)/(1024*1024))) + "M"
        libvirt_disk.create_disk("file", disk_format=disk_format, path=top_img, size=source_size, extra="-F %s -b %s" % (disk_format, source_file))

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        disks = vmxml.get_devices(device_type="disk")
        for disk in disks:
            vmxml.del_device(disk)
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", disk1_dict))
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", disk2_dict))
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", disk3_dict))
        vmxml.sync()

        if "--copy-storage-inc" in virsh_migrate_extra:
            copy_img_to_remote(params, [base1_img, base2_img, source_file])
        else:
            virsh.pool_create_as(pool_name, pool_type, pool_target, uri=dest_uri, ignore_status=True, debug=True)
            prepare_disks_remote(params, [base1_img, base2_img, source_file])
        prepare_disks_remote(params, [top_img], disk_size=source_size, extra="-F %s -b %s" % (disk_format, source_file))
        prepare_disks_remote(params, [top1_img], disk_size=disk_size, extra="-F %s -b %s" % (disk_format, base1_img))
        prepare_disks_remote(params, [top2_img], disk_size=disk_size, extra="-F %s -b %s" % (disk_format, base2_img))

        vm.start()
        vm.wait_for_login().close()

        # TODO: Need to wait for the system to fully boot, then the disk don't change.
        # Currently, there is no way to use utils_misc.wait_for(). Maybe we can replace
        # time.sleep() in the future.
        time.sleep(30)
        old_disk_info.update({"old_vdb_info": utils_misc.get_image_info(top1_img),
                             "old_vdc_info": utils_misc.get_image_info(top2_img)})
        if "--copy-storage-all" in virsh_migrate_extra:
            old_disk_info.update({"old_vda_info": utils_misc.get_image_info(source_file)})
        else:
            old_disk_info.update({"old_vda_info": utils_misc.get_image_info(top_img)})
        test.log.debug("old disk info: %s", old_disk_info)

    def setup_without_backing_chain():
        """
        Setup step for migration without backing chain case
        """
        mig_disk1 = params.get("mig_disk1")
        mig_disk2 = params.get("mig_disk2")
        disk_target1 = params.get("disk_target1")
        disk_target2 = params.get("disk_target2")
        disk1_dict = eval(params.get("disk1_dict"))
        disk2_dict = eval(params.get("disk2_dict"))

        test.log.info("Setup for migration without backing chain.")
        migration_obj.setup_connection()
        prepare_disks_local([mig_disk1, mig_disk2])

        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", disk1_dict))
        vmxml.add_device(libvirt_vmxml.create_vm_device_by_type("disk", disk2_dict))
        vmxml.sync()
        prepare_disks_remote(params, [mig_disk1, mig_disk2])
        libvirt_disk.create_remote_disk_by_same_metadata(vm, params)

        vm.start()
        vm.wait_for_login().close()
        source_file = vm.get_first_disk_devices()['source']
        old_disk_info.update({"old_vdb_info": utils_misc.get_image_info(mig_disk1),
                              "old_vdc_info": utils_misc.get_image_info(mig_disk2),
                              "old_vda_info": utils_misc.get_image_info(source_file)})
        test.log.debug("old disk info: %s", old_disk_info)

    def verify_with_backing_chain():
        """
        Verify step for migration with backing chain case
        """
        top_img = params.get("top_img")
        top1_img = params.get("top1_img")
        top2_img = params.get("top2_img")
        disk_diff_rate = float(params.get("disk_diff_rate"))

        test.log.debug("Verify step for backing chain case.")
        migration_obj.verify_default()
        check_disk_info(params, old_disk_info["old_vda_info"], top_img, test, disk_diff_rate)
        check_disk_info(params, old_disk_info["old_vdb_info"], top1_img, test, disk_diff_rate)
        check_disk_info(params, old_disk_info["old_vdc_info"], top2_img, test, disk_diff_rate)
        check_disk(params, vm)

    def verify_without_backing_chain():
        """
        Verify step for migration without backing chain case
        """
        mig_disk1 = params.get("mig_disk1")
        mig_disk2 = params.get("mig_disk2")
        disk_diff_rate = float(params.get("disk_diff_rate"))

        test.log.debug("Verify step for without backing chain case.")
        migration_obj.verify_default()
        source_file = vm.get_first_disk_devices()['source']
        check_disk_info(params, old_disk_info["old_vda_info"], source_file, test, disk_diff_rate=disk_diff_rate)
        check_disk_info(params, old_disk_info["old_vdb_info"], mig_disk1, test, disk_diff_rate)
        check_disk_info(params, old_disk_info["old_vdc_info"], mig_disk2, test, disk_diff_rate)
        check_disk(params, vm)

    def cleanup_with_backing_chain():
        """
        Cleanup step for migration with backing chain case
        """
        top_img = params.get("top_img")
        base1_img = params.get("base1_img")
        top1_img = params.get("top1_img")
        base2_img = params.get("base2_img")
        top2_img = params.get("top2_img")
        pool_name = params.get("target_pool_name")
        dest_uri = params.get("virsh_migrate_desturi")
        virsh_migrate_extra = params.get("virsh_migrate_extra")

        if "--copy-storage-all" in virsh_migrate_extra:
            virsh.pool_destroy(pool_name, ignore_status=True, debug=True, uri=dest_uri)
        migration_obj.cleanup_connection()
        for disk_img in [base1_img, top1_img, base2_img, top2_img, top_img]:
            libvirt.delete_local_disk("file", path=disk_img)
            remote.run_remote_cmd("rm -rf %s" % disk_img, params)

    def cleanup_without_backing_chain():
        """
        Cleanup step for migration without backing chain case
        """
        mig_disk1 = params.get("mig_disk1")
        mig_disk2 = params.get("mig_disk2")

        migration_obj.cleanup_connection()
        for disk_img in [mig_disk1, mig_disk2]:
            libvirt.delete_local_disk("file", path=disk_img)
            remote.run_remote_cmd("rm -rf %s" % disk_img, params)

    vm_name = params.get("migrate_main_vm")
    test_case = params.get("test_case")

    old_disk_info = {}
    old_top_info = {}

    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    setup_test = eval("setup_%s" % test_case) if "setup_%s" % test_case in \
        locals() else migration_obj.setup_connection
    verify_test = eval("verify_%s" % test_case) if "verify_%s" % test_case in \
        locals() else migration_obj.verify_default
    cleanup_test = eval("cleanup_%s" % test_case) if "cleanup_%s" % test_case in \
        locals() else migration_obj.cleanup_connection

    try:
        setup_test()
        migration_obj.run_migration()
        verify_test()
    finally:
        cleanup_test()
