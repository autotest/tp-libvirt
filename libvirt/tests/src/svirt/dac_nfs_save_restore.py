import os
import logging

from avocado.core import exceptions
from avocado.utils import process

from virttest import data_dir
from virttest import utils_selinux
from virttest import virt_vm
from virttest import virsh
from virttest import utils_config
from virttest import utils_libvirtd
from virttest.utils_test import libvirt as utlv
from virttest.libvirt_xml.vm_xml import VMXML


def check_ownership(file_path):
    """
    Return file ownership string user:group

    :params file_path: the file path
    :return: ownership string user:group or false when file not exist
    """
    try:
        f = os.open(file_path, 0)
    except OSError:
        return False
    stat_re = os.fstat(f)
    label = "%s:%s" % (stat_re.st_uid, stat_re.st_gid)
    os.close(f)
    logging.debug("File %s ownership is: %s" % (file_path, label))
    return label


def run(test, params, env):
    """
    Test DAC in save/restore domain to nfs pool.

    (1).Init variables for test.
    (2).Create nfs pool
    (3).Start VM and check result.
    (4).Save domain to the nfs pool.
    (5).Restore domain from the nfs file.
    """
    # Get general variables.
    status_error = ('yes' == params.get("status_error", 'no'))
    host_sestatus = params.get("dac_nfs_save_restore_host_selinux", "enforcing")
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
    export_options = params.get("export_options", "rw,async,no_root_squash")
    emulated_image = params.get("emulated_image")
    vol_name = params.get("vol_name")
    vol_format = params.get("vol_format")
    bk_file_name = params.get("bk_file_name")
    # Get pool file variables
    pre_file = "yes" == params.get("pre_file", "yes")
    pre_file_name = params.get("pre_file_name", "dac_nfs_file")
    file_tup = ("file_user", "file_group", "file_mode")
    file_val = []
    for i in file_tup:
        try:
            file_val.append(int(params.get(i)))
        except ValueError:
            test.cancel("%s value '%s' is not a number." %
                        (i, params.get(i)))
    # False positive - file_val was filled in the for loop above.
    # pylint: disable=E0632
    file_user, file_group, file_mode = file_val

    # Get variables about VM and get a VM object and VMXML instance.
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    # Backup domain disk label
    disks = vm.get_disk_devices()
    backup_labels_of_disks = {}
    for disk in list(disks.values()):
        disk_path = disk['source']
        f = os.open(disk_path, 0)
        stat_re = os.fstat(f)
        backup_labels_of_disks[disk_path] = "%s:%s" % (stat_re.st_uid,
                                                       stat_re.st_gid)
        os.close(f)

    # Backup selinux status of host.
    backup_sestatus = utils_selinux.get_status()

    pvt = None
    qemu_conf = utils_config.LibvirtQemuConfig()
    libvirtd = utils_libvirtd.Libvirtd()
    try:
        # chown domain disk mode to avoid fail on local disk
        for disk in list(disks.values()):
            disk_path = disk['source']
            if qemu_user == "root":
                os.chown(disk_path, 0, 0)
            elif qemu_user == "qemu":
                os.chown(disk_path, 107, 107)

        # Set selinux of host.
        utils_selinux.set_status(host_sestatus)

        # set qemu conf
        qemu_conf.user = qemu_user
        qemu_conf.group = qemu_user
        if dynamic_ownership:
            qemu_conf.dynamic_ownership = 1
        else:
            qemu_conf.dynamic_ownership = 0
        logging.debug("the qemu.conf content is: %s" % qemu_conf)
        libvirtd.restart()

        # Create dst pool for save/restore
        logging.debug("export_options is: %s" % export_options)
        pvt = utlv.PoolVolumeTest(test, params)
        pvt.pre_pool(pool_name, pool_type, pool_target,
                     emulated_image, image_size="1G",
                     pre_disk_vol=["20M"],
                     export_options=export_options)

        # Set virt_use_nfs
        result = process.run("setsebool virt_use_nfs %s" % virt_use_nfs, shell=True)
        if result.exit_status:
            test.cancel("Failed to set virt_use_nfs value")

        # Create a file on nfs server dir.
        tmp_dir = data_dir.get_data_dir()
        nfs_path = os.path.join(tmp_dir, nfs_server_dir)
        server_file_path = os.path.join(nfs_path, pre_file_name)
        if pre_file and not os.path.exists(server_file_path):
            open(server_file_path, 'a').close()
        if not pre_file and os.path.exists(server_file_path):
            test.cancel("File %s already exist in pool %s" %
                        (server_file_path, pool_name))

        # Get nfs mount file path
        mnt_path = os.path.join(tmp_dir, pool_target)
        mnt_file_path = os.path.join(mnt_path, pre_file_name)

        # Change img ownership and mode on nfs server dir
        if pre_file:
            os.chown(server_file_path, file_user, file_group)
            os.chmod(server_file_path, file_mode)

        # Start VM.
        try:
            vm.start()
            # Start VM successfully.
        except virt_vm.VMStartError as e:
            # Starting VM failed.
            test.fail("Domain failed to start. "
                      "error: %s" % e)

        label_before = check_ownership(server_file_path)
        if label_before:
            logging.debug("file ownership on nfs server before save: %s" %
                          label_before)

        # Save domain to nfs pool file
        save_re = virsh.save(vm_name, mnt_file_path, debug=True)
        if save_re.exit_status:
            if not status_error:
                test.fail("Failed to save domain to nfs pool file.")
        else:
            if status_error:
                test.fail("Save domain to nfs pool file succeeded, "
                          "expected Fail.")

        label_after = check_ownership(server_file_path)
        if label_after:
            logging.debug("file ownership on nfs server after save: %s"
                          % label_after)

        # Restore domain from the nfs pool file
        if not save_re.exit_status:
            restore_re = virsh.restore(mnt_file_path, debug=True)
            if restore_re.exit_status:
                if not status_error:
                    test.fail("Failed to restore domain from nfs "
                              "pool file.")
            else:
                if status_error:
                    test.fail("Restore domain from nfs pool file "
                              "succeeded, expected Fail.")

            label_after_rs = check_ownership(server_file_path)
            if label_after_rs:
                logging.debug("file ownership on nfs server after restore: %s"
                              % label_after_rs)

    finally:
        # clean up
        for path, label in list(backup_labels_of_disks.items()):
            label_list = label.split(":")
            os.chown(path, int(label_list[0]), int(label_list[1]))
        if pvt:
            try:
                pvt.cleanup_pool(pool_name, pool_type, pool_target,
                                 emulated_image)
            except exceptions.TestFail as detail:
                logging.error(str(detail))
        utils_selinux.set_status(backup_sestatus)
        qemu_conf.restore()
        libvirtd.restart()
