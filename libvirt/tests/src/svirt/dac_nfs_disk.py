import os
import re
import logging

from avocado.utils import process
from avocado.core import exceptions

from virttest import qemu_storage
from virttest import utils_selinux
from virttest import virt_vm
from virttest import virsh
from virttest import utils_config
from virttest import utils_libvirtd
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml import xcepts
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import data_dir


def check_ownership(file_path):
    """
    Return file ownership string user:group

    :params file_path: the file path
    :return: ownership string user:group or false when file not exist
    """
    try:
        f = os.open(file_path, os.O_RDONLY)
    except OSError:
        return False
    stat_re = os.fstat(f)
    label = "%s:%s" % (stat_re.st_uid, stat_re.st_gid)
    os.close(f)
    logging.debug("File %s ownership is: %s", file_path, label)
    return label


def run(test, params, env):
    """
    Test DAC in adding nfs pool disk to VM.

    (1).Init variables for test.
    (2).Create nfs pool and vol.
    (3).Attach the nfs pool vol to VM.
    (4).Start VM and check result.
    """
    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    host_sestatus = params.get("dac_nfs_disk_host_selinux", "enforcing")
    # Get qemu.conf config variables
    qemu_user = params.get("qemu_user")
    qemu_group = params.get("qemu_group")
    dynamic_ownership = "yes" == params.get("dynamic_ownership", "yes")
    # Get variables about pool vol
    virt_use_nfs = params.get("virt_use_nfs", "off")
    nfs_server_dir = params.get("nfs_server_dir", "nfs-server")
    pool_name = params.get("pool_name")
    pool_type = params.get("pool_type")
    pool_target = params.get("pool_target")
    export_options = params.get("export_options",
                                "rw,async,no_root_squash")
    emulated_image = params.get("emulated_image")
    vol_name = params.get("vol_name")
    vol_format = params.get("vol_format")
    bk_file_name = params.get("bk_file_name")
    # Get pool vol variables
    img_tup = ("img_user", "img_group", "img_mode")
    img_val = []
    for i in img_tup:
        try:
            img_val.append(int(params.get(i)))
        except ValueError:
            test.cancel("%s value '%s' is not a number." %
                        (i, params.get(i)))
    # False positive - img_val was filled in the for loop above.
    # pylint: disable=E0632
    img_user, img_group, img_mode = img_val

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    vm_os_xml = vmxml.os

    # Backup domain disk label
    disks = vm.get_disk_devices()
    backup_labels_of_disks = {}
    for disk in list(disks.values()):
        disk_path = disk['source']
        label = check_ownership(disk_path)
        if label:
            backup_labels_of_disks[disk_path] = label

    try:
        if vm_os_xml.nvram:
            nvram_path = vm_os_xml.nvram
            if not os.path.exists(nvram_path):
                # Need libvirt automatically generate the path
                vm.start()
                vm.destroy(gracefully=False)
            label = check_ownership(nvram_path)
            if label:
                backup_labels_of_disks[nvram_path] = label
    except xcepts.LibvirtXMLNotFoundError:
        logging.debug("vm xml don't have nvram element")

    # Backup selinux status of host.
    backup_sestatus = utils_selinux.get_status()

    pvt = None
    snapshot_name = None
    disk_snap_path = []
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        # chown domain disk to qemu:qemu to avoid fail on local disk
        for file_path in list(backup_labels_of_disks.keys()):
            if qemu_user == "root":
                os.chown(file_path, 0, 0)
            elif qemu_user == "qemu":
                os.chown(file_path, 107, 107)
            else:
                process.run('chown %s %s' % (qemu_user, file_path), shell=True)

        # Set selinux of host.
        if backup_sestatus == "disabled":
            test.cancel("SELinux is in Disabled mode."
                        "It must be Enabled to"
                        "run this test")
        utils_selinux.set_status(host_sestatus)

        # set qemu conf
        qemu_conf.user = qemu_user
        qemu_conf.group = qemu_user
        if dynamic_ownership:
            qemu_conf.dynamic_ownership = 1
        else:
            qemu_conf.dynamic_ownership = 0
        logging.debug("the qemu.conf content is: %s", qemu_conf)
        libvirtd.restart()

        # Create dst pool for create attach vol img
        logging.debug("export_options is: %s", export_options)
        pvt = utlv.PoolVolumeTest(test, params)
        pvt.pre_pool(pool_name, pool_type, pool_target,
                     emulated_image, image_size="1G",
                     pre_disk_vol=["20M"],
                     export_options=export_options)

        # set virt_use_nfs
        result = process.run("setsebool virt_use_nfs %s" % virt_use_nfs,
                             shell=True)
        if result.exit_status:
            test.cancel("Failed to set virt_use_nfs value")

        # Init a QemuImg instance and create img on nfs server dir.
        params['image_name'] = vol_name
        tmp_dir = data_dir.get_data_dir()
        nfs_path = os.path.join(tmp_dir, nfs_server_dir)
        image = qemu_storage.QemuImg(params, nfs_path, vol_name)
        # Create a image.
        server_img_path, result = image.create(params)

        if params.get("image_name_backing_file"):
            params['image_name'] = bk_file_name
            params['has_backing_file'] = "yes"
            image = qemu_storage.QemuImg(params, nfs_path, bk_file_name)
            server_img_path, result = image.create(params)

        # Get vol img path
        vol_name = server_img_path.split('/')[-1]
        virsh.pool_refresh(pool_name, debug=True)
        cmd_result = virsh.vol_path(vol_name, pool_name, debug=True)
        if cmd_result.exit_status:
            test.cancel("Failed to get volume path from pool.")
        img_path = cmd_result.stdout.strip()

        # Do the attach action.
        extra = "--persistent --subdriver qcow2"
        result = virsh.attach_disk(vm_name, source=img_path, target="vdf",
                                   extra=extra, debug=True)
        if result.exit_status:
            test.fail("Failed to attach disk %s to VM."
                      "Detail: %s." % (img_path, result.stderr))

        # Change img ownership and mode on nfs server dir
        os.chown(server_img_path, img_user, img_group)
        os.chmod(server_img_path, img_mode)

        img_label_before = check_ownership(server_img_path)
        if img_label_before:
            logging.debug("attached image ownership on nfs server before "
                          "start: %s", img_label_before)

        # Start VM to check the VM is able to access the image or not.
        try:
            vm.start()
            # Start VM successfully.

            img_label_after = check_ownership(server_img_path)
            if img_label_after:
                logging.debug("attached image ownership on nfs server after"
                              " start: %s", img_label_after)

            if status_error:
                test.fail('Test succeeded in negative case.')
        except virt_vm.VMStartError as e:
            # Starting VM failed.
            if not status_error:
                test.fail("Test failed in positive case."
                          "error: %s" % e)

        if params.get("image_name_backing_file"):
            options = "--disk-only"
            snapshot_result = virsh.snapshot_create(vm_name, options,
                                                    debug=True)
            if snapshot_result.exit_status:
                if not status_error:
                    test.fail("Failed to create snapshot. Error:%s."
                              % snapshot_result.stderr.strip())
            snapshot_name = re.search(
                "\d+", snapshot_result.stdout.strip()).group(0)

        if snapshot_name:
            disks_snap = vm.get_disk_devices()
            for disk in list(disks_snap.values()):
                disk_snap_path.append(disk['source'])
            virsh.snapshot_delete(vm_name, snapshot_name, "--metadata",
                                  debug=True)

        try:
            virsh.detach_disk(vm_name, target="vdf", extra="--persistent",
                              debug=True)
        except process.CmdError:
            test.fail("Detach disk 'vdf' from VM %s failed."
                      % vm.name)
    finally:
        # clean up
        vm.destroy()
        qemu_conf.restore()
        for path, label in list(backup_labels_of_disks.items()):
            label_list = label.split(":")
            os.chown(path, int(label_list[0]), int(label_list[1]))
        if snapshot_name:
            backup_xml.sync("--snapshots-metadata")
        else:
            backup_xml.sync()
        for i in disk_snap_path:
            if i and os.path.exists(i):
                os.unlink(i)
        if pvt:
            try:
                pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                 emulated_image)
            except exceptions.TestFail as detail:
                logging.error(str(detail))
        utils_selinux.set_status(backup_sestatus)
        libvirtd.restart()
