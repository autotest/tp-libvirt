import logging
import time
import shutil
import os

from avocado.utils import process
from avocado.utils import distro

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest import utils_package
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.staging.service import Factory


def run(test, params, env):
    """
    Test virsh domblkerror in 2 types error
    1. unspecified error
    2. no space
    """

    if not virsh.has_help_command('domblkerror'):
        test.cancel("This version of libvirt does not support domblkerror "
                    "test")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    error_type = params.get("domblkerror_error_type")
    timeout = params.get("domblkerror_timeout", 240)
    mnt_dir = params.get("domblkerror_mnt_dir", "/home/test")
    export_file = params.get("nfs_export_file", "/etc/exports")
    img_name = params.get("domblkerror_img_name", "libvirt-disk")
    img_size = params.get("domblkerror_img_size")
    target_dev = params.get("domblkerror_target_dev", "vdb")
    pool_name = params.get("domblkerror_pool_name", "fs_pool")
    vol_name = params.get("domblkerror_vol_name", "vol1")
    ubuntu = distro.detect().name == 'Ubuntu'
    rhel = distro.detect().name == 'rhel'
    nfs_service_package = params.get("nfs_service_package", "nfs-kernel-server")
    nfs_service = None
    selinux_bool = None
    session = None
    selinux_bak = ""

    vm = env.get_vm(vm_name)
    if error_type == "unspecified error":
        selinux_local = params.get("setup_selinux_local", "yes") == "yes"
        if not ubuntu and not rhel:
            nfs_service_package = "nfs"
        elif rhel:
            nfs_service_package = "nfs-server"
        if not rhel and not utils_package.package_install(nfs_service_package):
            test.cancel("NFS package not available in host to test")
        # backup /etc/exports
        shutil.copyfile(export_file, "%s.bak" % export_file)
    # backup xml
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

    try:
        # Gerenate tmp dir
        tmp_dir = data_dir.get_tmp_dir()
        img_dir = os.path.join(tmp_dir, 'images')
        if not os.path.exists(img_dir):
            os.mkdir(img_dir)
        # Generate attached disk
        process.run("qemu-img create %s %s" %
                    (os.path.join(img_dir, img_name), img_size),
                    shell=True, verbose=True)

        # Get unspecified error
        if error_type == "unspecified error":
            # In this situation, guest will attach a disk on nfs, stop nfs
            # service will cause guest paused and get unspecified error
            nfs_dir = os.path.join(tmp_dir, 'mnt')
            if not os.path.exists(nfs_dir):
                os.mkdir(nfs_dir)
            mount_opt = "rw,no_root_squash,async"
            res = libvirt.setup_or_cleanup_nfs(is_setup=True,
                                               mount_dir=nfs_dir,
                                               is_mount=False,
                                               export_options=mount_opt,
                                               export_dir=img_dir)
            if not ubuntu:
                selinux_bak = res["selinux_status_bak"]
            process.run("mount -o nolock,soft,timeo=1,retrans=1,retry=0 "
                        "127.0.0.1:%s %s" % (img_dir, nfs_dir), shell=True,
                        verbose=True)
            img_path = os.path.join(nfs_dir, img_name)
            nfs_service = Factory.create_service(nfs_service_package)
            if not ubuntu and selinux_local:
                params['set_sebool_local'] = "yes"
                params['local_boolean_varible'] = "virt_use_nfs"
                params['local_boolean_value'] = "on"
                selinux_bool = utils_misc.SELinuxBoolean(params)
                selinux_bool.setup()

        elif error_type == "no space":
            # Steps to generate no space block error:
            # 1. Prepare a iscsi disk and build fs pool with it
            # 2. Create vol with larger capacity and 0 allocation
            # 3. Attach this disk in guest
            # 4. In guest, create large image in the vol, which may cause
            # guest paused

            _pool_vol = None
            pool_target = os.path.join(tmp_dir, pool_name)
            _pool_vol = libvirt.PoolVolumeTest(test, params)
            _pool_vol.pre_pool(pool_name, "fs", pool_target, img_name,
                               image_size=img_size)
            _pool_vol.pre_vol(vol_name, "raw", "100M", "0", pool_name)
            img_path = os.path.join(pool_target, vol_name)

        # Generate disk xml
        # Guest will attach a disk with cache=none and error_policy=stop
        img_disk = Disk(type_name="file")
        img_disk.device = "disk"
        img_disk.source = img_disk.new_disk_source(
            **{'attrs': {'file': img_path}})
        img_disk.driver = {'name': "qemu",
                           'type': "raw",
                           'cache': "none",
                           'error_policy': "stop"}
        img_disk.target = {'dev': target_dev,
                           'bus': "virtio"}
        logging.debug("disk xml is %s", img_disk.xml)

        # Start guest and get session
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        # Get disk list before operation
        get_disks_cmd = "fdisk -l|grep '^Disk /dev'|cut -d: -f1|cut -d' ' -f2"
        bef_list = str(session.cmd_output(get_disks_cmd)).strip().split("\n")
        logging.debug("disk_list_debug = %s", bef_list)

        # Attach disk to guest
        ret = virsh.attach_device(vm_name, img_disk.xml)
        if ret.exit_status != 0:
            test.fail("Fail to attach device %s" % ret.stderr)
        time.sleep(2)
        logging.debug("domain xml is %s", virsh.dumpxml(vm_name))
        # get disk list after attach
        aft_list = str(session.cmd_output(get_disks_cmd)).strip().split("\n")
        logging.debug("disk list after attaching - %s", aft_list)
        # Find new disk after attach
        new_disk = "".join(list(set(bef_list) ^ set(aft_list)))
        logging.debug("new disk is %s", new_disk)

        def create_large_image():
            """
            Create large image in guest
            """
            # install dependent packages
            pkg_list = ["parted", "e2fsprogs"]
            for pkg in pkg_list:
                if not utils_package.package_install(pkg, session):
                    test.error("Failed to install dependent package %s" % pkg)

            # create partition and file system
            session.cmd("parted -s %s mklabel msdos" % new_disk)
            session.cmd("parted -s %s mkpart primary ext3 '0%%' '100%%'" %
                        new_disk)
            # mount disk and write file in it
            session.cmd("mkfs.ext3 %s1" % new_disk)
            session.cmd("mkdir -p %s && mount %s1 %s" %
                        (mnt_dir, new_disk, mnt_dir))

            # The following step may cause guest paused before it return
            try:
                session.cmd("dd if=/dev/zero of=%s/big_file bs=1024 "
                            "count=51200 && sync" % mnt_dir)
            except Exception as err:
                logging.debug("Expected Fail %s", err)
            session.close()

        create_large_image()
        if error_type == "unspecified error":
            # umount nfs to trigger error after create large image
            if nfs_service is not None:
                nfs_service.stop()
                logging.debug("nfs status is %s", nfs_service.status())

        # wait and check the guest status with timeout
        def _check_state():
            """
            Check domain state
            """
            return (vm.state() == "paused")

        if not utils_misc.wait_for(_check_state, timeout):
            # If not paused, perform one more IO operation to the mnt disk
            session = vm.wait_for_login()
            session.cmd("echo 'one more write to big file' > %s/big_file" % mnt_dir)
            if not utils_misc.wait_for(_check_state, 60):
                test.fail("Guest does not paused, it is %s now" % vm.state())
        else:
            logging.info("Now domain state changed to paused status")
            output = virsh.domblkerror(vm_name)
            if output.exit_status == 0:
                expect_result = "%s: %s" % (img_disk.target['dev'], error_type)
                if output.stdout.strip() == expect_result:
                    logging.info("Get expect result: %s", expect_result)
                else:
                    test.fail("Failed to get expect result, get %s" %
                              output.stdout.strip())
            else:
                test.fail("Fail to get domblkerror info:%s" % output.stderr)
    finally:
        logging.info("Do clean steps")
        if session:
            session.close()
        if error_type == "unspecified error":
            if nfs_service is not None:
                nfs_service.start()
            vm.destroy()
            if os.path.isfile("%s.bak" % export_file):
                shutil.move("%s.bak" % export_file, export_file)
            res = libvirt.setup_or_cleanup_nfs(is_setup=False,
                                               mount_dir=nfs_dir,
                                               export_dir=img_dir,
                                               restore_selinux=selinux_bak)
            if selinux_bool:
                selinux_bool.cleanup(keep_authorized_keys=True)
        elif error_type == "no space":
            vm.destroy()
            if _pool_vol:
                _pool_vol.cleanup_pool(pool_name, "fs", pool_target, img_name)
        vmxml_backup.sync()
        data_dir.clean_tmp_files()
