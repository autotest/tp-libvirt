import logging
import time
import shutil
import os
from autotest.client.shared import error
from autotest.client import utils
from virttest import virsh, data_dir, utils_test, utils_misc
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
        raise error.TestNAError("This version of libvirt does not support "
                                "domblkerror test")

    vm_name = params.get("main_vm", "virt-tests-vm1")
    error_type = params.get("domblkerror_error_type")
    timeout = params.get("domblkerror_timeout", 120)
    mnt_dir = params.get("domblkerror_mnt_dir", "/home/test")
    tmp_file = params.get("domblkerror_tmp_file", "/tmp/fdisk-cmd")
    export_file = params.get("nfs_export_file", "/etc/exports")
    img_name = params.get("domblkerror_img_name", "libvirt_disk")
    img_size = params.get("domblkerror_img_size")
    target_dev = params.get("domblkerror_target_dev", "vdb")
    pool_name = params.get("domblkerror_pool_name", "fs_pool")
    vol_name = params.get("domblkerror_vol_name", "vol1")
    loop_dev = params.get("domblkerror_loop_dev", "/dev/loop0")

    vm = env.get_vm(vm_name)
    # backup /etc/exports
    shutil.copyfile(export_file, "%s.bak" % export_file)

    try:
        # Gerenate tmp dir
        tmp_dir = data_dir.get_tmp_dir()
        img_dir = os.path.join(tmp_dir, 'images')
        if not os.path.exists(img_dir):
            os.mkdir(img_dir)
        # Generate attached disk
        utils.run("qemu-img create %s %s" %
                  (os.path.join(img_dir, img_name), img_size))

        # Get unspecified error
        if error_type == "unspecified error":
            # In this situation, guest will attach a disk on nfs, stop nfs
            # service will cause guest paused and get unspecified error
            nfs_dir = os.path.join(tmp_dir, 'mnt')
            if not os.path.exists(nfs_dir):
                os.mkdir(nfs_dir)
            mount_opt = "rw,no_root_squash,async"
            utils_test.libvirt.setup_or_cleanup_nfs(True, nfs_dir, False,
                                                    mount_opt, img_dir)
            utils.run("mount -o soft,timeo=1,retrans=1,retry=0 localhost:%s "
                      "%s" % (img_dir, nfs_dir))
            img_path = os.path.join(nfs_dir, img_name)
            nfs_service = Factory.create_service("nfs")

        elif error_type == "no space":
            # Steps to generate no space block error:
            # 1. Prepare a iscsi disk and build fs pool with it
            # 2. Create vol with larger capacity and 0 allocation
            # 3. Attach this disk in guest
            # 4. In guest, create large image in the vol, which may cause
            # guest paused

            pool_target = os.path.join(tmp_dir, pool_name)
            _pool_vol = utils_test.libvirt.PoolVolumeTest(test, params)
            _pool_vol.pre_pool(pool_name, "fs", pool_target, img_name,
                               img_size)
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
        bef_list = session.cmd_output(get_disks_cmd).split("\n")

        # Attach disk to guest
        ret = virsh.attach_device(domain_opt=vm_name,
                                  file_opt=img_disk.xml)
        if ret.exit_status != 0:
            raise error.TestFail("Fail to attach device %s" % ret.stderr)
        time.sleep(2)
        logging.debug("domain xml is %s", virsh.dumpxml(vm_name))
        # get disk list after attach
        aft_list = session.cmd_output(get_disks_cmd).split("\n")
        # Find new disk after attach
        new_disk = "".join(list(set(bef_list) ^ set(aft_list)))
        logging.debug("new disk is %s", new_disk)

        def create_large_image():
            """
            Create large image in guest
            """
            # create partition and file system
            session.cmd("echo 'n\np\n\n\n\nw\n' > %s" % tmp_file)
            # mount disk and write file in it
            try:
                # The following steps may cause guest paused before it return
                session.cmd("fdisk %s < %s" % (new_disk, tmp_file))
                session.cmd("mkfs.ext3 %s1" % new_disk)
                session.cmd("mkdir -p %s && mount %s1 %s" %
                            (mnt_dir, new_disk, mnt_dir))
                session.cmd("dd if=/dev/zero of=%s/big_file bs=1024 "
                            "count=51200 && sync" % mnt_dir)
            except Exception, err:
                logging.debug("Expected Fail %s" % err)
            session.close()

        create_large_image()
        if error_type == "unspecified error":
            # umount nfs to trigger error after create large image
            nfs_service.stop()
            logging.debug("nfs status is %s", nfs_service.status())

        # wait and check the guest status with timeout
        def _check_state():
            """
            Check domain state
            """
            return (vm.state() == "paused")

        if not utils_misc.wait_for(_check_state, timeout):
            raise error.TestFail("Guest does not paused, it is %s now" %
                                 vm.state())
        else:
            logging.info("Now domain state changed to paused status")
            output = virsh.domblkerror(vm_name)
            if output.exit_status == 0:
                expect_result = "%s: %s" % (img_disk.target['dev'], error_type)
                if output.stdout.strip() == expect_result:
                    logging.info("Get expect result: %s", expect_result)
                else:
                    raise error.TestFail("Failed to get expect result, get %s"
                                         % output.stdout.strip())
            else:
                raise error.TestFail("Fail to get domblkerror info:%s" %
                                     output.stderr)
    finally:
        logging.info("Do clean steps")
        try:
            if error_type == "unspecified error":
                nfs_service.start()
                vm.destroy()
                if os.path.isfile("%s.bak" % export_file):
                    shutil.move("%s.bak" % export_file, export_file)
                utils.run("umount %s" % nfs_dir)
            elif error_type == "no space":
                vm.destroy()
                _pool_vol.cleanup_pool(pool_name, "fs", pool_target, img_name)
        finally:
            utils.run("rm -rf %s" % tmp_dir)
