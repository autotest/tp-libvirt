import os
import re
import logging

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import utils_npiv
from virttest import libvirt_vm
from virttest import libvirt_storage
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import disk
from virttest.utils_test import libvirt as utlv


_TIMEOUT = 5
_BY_PATH_DIR = "/dev/disk/by-path"


def get_symbols_by_blk(blkdev, method="by-path"):
    """
    Find the lun device name under /dev/disk/by-path for specified
    block device.

    :params blkdev: the name of the block device, such as sda, sdb.
    :params method: find under which folder under /dev/disk, should
     be one of: by-path, by-id, by-uuid.
    :return: the blkdev's sl name under /dev/disk/[by-id|by-uuid|by-path]
    """
    symbolic_links = []
    dir_path = "/dev/disk/" + method
    if not os.path.exists(dir_path):
        raise exceptions.TestFail("Dir path %s does not exist!", dir_path)
    logging.debug("dir_path=%s, blkdev=%s", dir_path, blkdev)
    try:
        cmd = "ls -al %s | grep %s | grep -v %s\[1-9\] |"
        cmd += "awk '{FS=\" \"} {for (f=1; f<=NF; f+=1) "
        cmd += "{if ($f ~ /pci/){print $f}}}'"
        cmd %= (dir_path, blkdev, blkdev)
        result = process.run(cmd, shell=True)
    except process.cmdError as detail:
        raise exceptions.TestError(str(detail))
    symbolic_links = result.stdout_text.strip().splitlines()
    return symbolic_links


def get_blks_by_scsi(scsi_bus, blk_prefix="sd"):
    """
    Find the scsi block devices under /dev/, with specific scsi bus number

    :params scs_bus: the scsi bus number
    :params blk_prefix: such as "sd", "vd"
    :return: the block devices' name list
    """

    blk_names = []
    # utils_npiv.restart_multipathd()
    cmd = "multipath -ll | grep '\- %s:' | grep 'ready running' |"
    cmd += "awk '{FS=\" \"}{for (f=1; f<=NF; f+=1)"
    cmd += "{if ($f ~ /%s/) {print $f}}}'"
    cmd %= (scsi_bus, blk_prefix)
    try:
        result = process.run(cmd, shell=True)
        logging.debug("multipath result: %s", result.stdout_text.strip())
    except process.cmdError as detail:
        raise exceptions.TestError(str(detail))
    blk_names = result.stdout_text.strip().splitlines()
    return blk_names


def prepare_scsi_pool(pool_name, wwnn, wwpn, parent_scsi, pool_target):
    """
    Create a scsi pool with pool-create-as

    :params pool_name: name of the pool
    :params wwnn: wwnn to be used for vhba
    :params wwpn: wwpn to be used for vhba
    :params parent_scsi: vhba will be created under parent scsi_host
    :params pool_target: the target dir of the pool
    """
    extra = "--adapter-wwnn %s --adapter-wwpn %s "
    extra += "--adapter-parent %s"
    extra %= (wwnn, wwpn, parent_scsi)
    if not virsh.pool_create_as(pool_name, "scsi", pool_target, extra):
        raise exceptions.TestFail("Failed to prepare pool:%s", pool_name)


def create_file_in_vm(session, file_path, file_content="test"):
    """
    Create a file in vm

    :params session: the vm's session
    :params file_path: the path to the file to be created in vm
    :params file_content: content to be written into that file
    """
    status_1, output_1 = session.cmd_status_output("echo '%s' > %s"
                                                   % (file_content, file_path))
    status_2, output_2 = session.cmd_status_output("cat %s | grep %s"
                                                   % (file_path, file_content))
    if status_1 or status_2:
        raise exceptions.TestFail("Failed to create file %s", file_path)


def get_file_in_vm(session, file_path):
    """
    Get the file existence and content in vm

    :params session: the vm's session
    :params file_path: the path to the file in vm
    :return: the file's existence and content
    """
    file_existence = False
    file_content = None
    cmd = "cat %s" % file_path
    status, output = session.cmd_status_output(cmd)
    if not status:
        file_existence = True
        file_content = output
    return file_existence, file_content


def mkfs_and_mount(session, mount_disk):
    """
    Mkfs a block device and mount it to /mnt in vm

    :params session: vm's session
    :params mount_disk: the disk which'll be mkfs'ed and mounted
    """
    logging.debug(session.cmd_output('lsblk'))
    status, output = session.cmd_status_output('mkfs.ext4 %s -F'
                                               % mount_disk)
    logging.debug("mount result: %s", output)
    if status:
        raise exceptions.TestFail("Failed to mkfs disk %s in vm", mount_disk)
    session.cmd_status_output("umount -f /mnt")
    status, output = session.cmd_status_output("mount %s /mnt" % mount_disk)
    logging.debug("mount result: %s", output)
    if status:
        raise exceptions.TestFail("Failed to mount disk %s in vm", mount_disk)


def run(test, params, env):
    """
    1. prepare a fc lun with one of following methods
        - create a scsi pool&vol
        - create a vhba
    2. prepare the virtual disk xml, as one of following
        - source = /dev/disk/by-path
        - source = /dev/mapper/mpathX
        - source = pool&vol format
    3. start a vm with above disk as vdb
    4. create disk-only snapshot of vdb
    5. check the snapshot-list and snapshot file's existence
    6. mount vdb and touch file to it
    7. revert the snapshot and check file's existence
    8. delete snapshot
    9. cleanup env.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    wwpn = params.get("wwpn", "WWPN_EXAMPLE")
    wwnn = params.get("wwnn", "WWNN_EXAMPLE")
    disk_device = params.get("disk_device", "disk")
    disk_type = params.get("disk_type", "file")
    disk_size = params.get("disk_size", "100M")
    device_target = params.get("device_target", "vdb")
    driver_name = params.get("driver_name", "qemu")
    driver_type = params.get("driver_type", "raw")
    target_bus = params.get("target_bus", "virtio")
    vd_format = params.get("vd_format", "")
    snapshot_dir = params.get("snapshot_dir", "/tmp")
    snapshot_name = params.get("snapshot_name", "s1")
    pool_name = params.get("pool_name", "")
    pool_target = params.get("pool_target", "/dev")
    snapshot_disk_only = "yes" == params.get("snapshot_disk_only", "no")
    new_vhbas = []
    current_vhbas = []
    new_vhba = []
    path_to_blk = ""
    lun_sl = []
    new_disk = ""
    pool_ins = None
    old_mpath_conf = ""
    mpath_conf_path = "/etc/multipath.conf"
    original_mpath_conf_exist = os.path.exists(mpath_conf_path)

    vm = env.get_vm(vm_name)
    online_hbas = utils_npiv.find_hbas("hba")
    if not online_hbas:
        raise exceptions.TestSkipError("There is no online hba cards.")
    old_mpath_conf = utils_npiv.prepare_multipath_conf(conf_path=mpath_conf_path,
                                                       replace_existing=True)
    first_online_hba = online_hbas[0]
    old_vhbas = utils_npiv.find_hbas("vhba")
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    virt_vm = libvirt_vm.VM(vm_name, vm.params, vm.root_dir,
                            vm.address_cache)
    old_disks = virt_vm.get_disks()

    if vm.is_alive():
        vm.destroy(gracefully=False)
    if pool_name:
        pool_ins = libvirt_storage.StoragePool()
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    try:
        # prepare a fc lun
        if vd_format in ['scsi_vol']:
            if pool_ins.pool_exists(pool_name):
                raise exceptions.TestFail("Pool %s already exist" % pool_name)
            prepare_scsi_pool(pool_name, wwnn, wwpn,
                              first_online_hba, pool_target)
            utils_misc.wait_for(lambda: utils_npiv.is_vhbas_added(old_vhbas),
                                timeout=_TIMEOUT)
            if not utils_npiv.is_vhbas_added(old_vhbas):
                raise exceptions.TestFail("vHBA not successfully created")
            current_vhbas = utils_npiv.find_hbas("vhba")
            new_vhba = list(set(current_vhbas).difference(
                set(old_vhbas)))[0]
            new_vhbas.append(new_vhba)
            new_vhba_scsibus = re.sub("\D", "", new_vhba)
            utils_misc.wait_for(lambda: get_blks_by_scsi(new_vhba_scsibus),
                                timeout=_TIMEOUT)
            new_blks = get_blks_by_scsi(new_vhba_scsibus)
            if not new_blks:
                raise exceptions.TestFail("block device not found with scsi_%s",
                                          new_vhba_scsibus)
            vol_list = utlv.get_vol_list(pool_name, vol_check=True,
                                         timeout=_TIMEOUT*3)
            path_to_blk = list(vol_list.values())[0]
        elif vd_format in ['mpath', 'by_path']:
            old_mpath_devs = utils_npiv.find_mpath_devs()
            new_vhba = utils_npiv.nodedev_create_from_xml(
                    {"nodedev_parent": first_online_hba,
                     "scsi_wwnn": wwnn,
                     "scsi_wwpn": wwpn})
            utils_misc.wait_for(
                lambda: utils_npiv.is_vhbas_added(old_vhbas),
                timeout=_TIMEOUT*2)
            if not new_vhba:
                raise exceptions.TestFail("vHBA not sucessfully generated.")
            new_vhbas.append(new_vhba)
            if vd_format == "mpath":
                utils_misc.wait_for(
                    lambda: utils_npiv.is_mpath_devs_added(old_mpath_devs),
                    timeout=_TIMEOUT*5)
                if not utils_npiv.is_mpath_devs_added(old_mpath_devs):
                    raise exceptions.TestFail("mpath dev not generated.")
                cur_mpath_devs = utils_npiv.find_mpath_devs()
                new_mpath_devs = list(set(cur_mpath_devs).difference(
                    set(old_mpath_devs)))
                logging.debug("The newly added mpath dev is: %s",
                              new_mpath_devs)
                path_to_blk = "/dev/mapper/" + new_mpath_devs[0]
            elif vd_format == "by_path":
                new_vhba_scsibus = re.sub("\D", "", new_vhba)
                utils_misc.wait_for(lambda: get_blks_by_scsi(new_vhba_scsibus),
                                    timeout=_TIMEOUT)
                new_blks = get_blks_by_scsi(new_vhba_scsibus)
                if not new_blks:
                    raise exceptions.TestFail("blk dev not found with scsi_%s",
                                              new_vhba_scsibus)
                first_blk_dev = new_blks[0]
                utils_misc.wait_for(
                    lambda: get_symbols_by_blk(first_blk_dev),
                    timeout=_TIMEOUT)
                lun_sl = get_symbols_by_blk(first_blk_dev)
                if not lun_sl:
                    raise exceptions.TestFail("lun symbolic links not found in "
                                              "/dev/disk/by-path/ for %s" %
                                              first_blk_dev)
                lun_dev = lun_sl[0]
                path_to_blk = os.path.join(_BY_PATH_DIR, lun_dev)
            else:
                pass
        else:
            raise exceptions.TestSkipError("Not provided how to pass"
                                           "virtual disk to VM.")

        # create qcow2 file on the block device with specified size
        if path_to_blk:
            cmd = "qemu-img create -f qcow2 %s %s" % (path_to_blk, disk_size)
            try:
                process.run(cmd, shell=True)
            except process.cmdError as detail:
                raise exceptions.TestFail("Fail to create qcow2 on blk dev: %s",
                                          detail)
        else:
            raise exceptions.TestFail("Don't have a vaild path to blk dev.")

        # prepare disk xml
        if "vol" in vd_format:
            vol_list = utlv.get_vol_list(pool_name, vol_check=True,
                                         timeout=_TIMEOUT*3)
            test_vol = list(vol_list.keys())[0]
            disk_params = {'type_name': disk_type,
                           'target_dev': device_target,
                           'target_bus': target_bus,
                           'source_pool': pool_name,
                           'source_volume': test_vol,
                           'driver_type': driver_type}
        else:
            disk_params = {'type_name': disk_type,
                           'device': disk_device,
                           'driver_name': driver_name,
                           'driver_type': driver_type,
                           'source_file': path_to_blk,
                           'target_dev': device_target,
                           'target_bus': target_bus}
        if vm.is_alive():
            vm.destroy(gracefully=False)
        new_disk = disk.Disk()
        new_disk.xml = open(utlv.create_disk_xml(disk_params)).read()

        # start vm with the virtual disk
        vmxml.devices = vmxml.devices.append(new_disk)
        vmxml.sync()
        vm.start()
        session = vm.wait_for_login()
        cur_disks = virt_vm.get_disks()
        mount_disk = "".join(list(set(old_disks) ^ set(cur_disks)))

        # mkfs and mount disk in vm, create a file on that disk.
        if not mount_disk:
            logging.debug("old_disk: %s, new_disk: %s", old_disks, cur_disks)
            raise exceptions.TestFail("No new disk found in vm.")
        mkfs_and_mount(session, mount_disk)
        create_file_in_vm(session, "/mnt/before_snapshot.txt", "before")

        # virsh snapshot-create-as vm s --disk-only --diskspec vda,file=path
        if snapshot_disk_only:
            vm_blks = list(vm.get_disk_devices().keys())
            options = "%s --disk-only" % snapshot_name
            for vm_blk in vm_blks:
                snapshot_file = snapshot_dir + "/" + vm_blk + "." + snapshot_name
                if os.path.exists(snapshot_file):
                    os.remove(snapshot_file)
                options = options + " --diskspec %s,file=%s" % (vm_blk,
                                                                snapshot_file)
        else:
            options = snapshot_name
        utlv.check_exit_status(virsh.snapshot_create_as(vm_name, options))

        # check virsh snapshot-list
        logging.debug("Running: snapshot-list %s", vm_name)
        snapshot_list = virsh.snapshot_list(vm_name)
        logging.debug("snapshot list is: %s", snapshot_list)
        if not snapshot_list:
            raise exceptions.TestFail("snapshots not found after creation.")

        # snapshot-revert doesn't support external snapshot for now. so
        # only check this with internal snapshot.
        if not snapshot_disk_only:
            create_file_in_vm(session, "/mnt/after_snapshot.txt", "after")
            logging.debug("Running: snapshot-revert %s %s",
                          vm_name, snapshot_name)
            utlv.check_exit_status(virsh.snapshot_revert(vm_name, snapshot_name))
            session = vm.wait_for_login()
            file_existence, file_content = get_file_in_vm(session,
                                                          "/mnt/after_snapshot.txt")
            logging.debug("file exist = %s, file content = %s",
                          file_existence, file_content)
            if file_existence:
                raise exceptions.TestFail("The file created "
                                          "after snapshot still exists.")
            file_existence, file_content = get_file_in_vm(session,
                                                          "/mnt/before_snapshot.txt")
            logging.debug("file eixst = %s, file content = %s",
                          file_existence, file_content)
            if ((not file_existence) or (file_content.strip() != "before")):
                raise exceptions.TestFail("The file created "
                                          "before snapshot is lost.")
        # delete snapshots
            # if diskonly, delete --metadata and remove files
            # if not diskonly, delete snapshot
        if snapshot_disk_only:
            options = "--metadata"
        else:
            options = ""
        for snap in snapshot_list:
            logging.debug("deleting snapshot %s with options %s",
                          snap, options)
            result = virsh.snapshot_delete(vm_name, snap, options)
            logging.debug("result of snapshot-delete: %s",
                          result.stdout.strip())
            if snapshot_disk_only:
                vm_blks = list(vm.get_disk_devices().keys())
                for vm_blk in vm_blks:
                    snapshot_file = snapshot_dir + "/" + vm_blk + "." + snap
                    if os.path.exists(snapshot_file):
                        os.remove(snapshot_file)
        snapshot_list = virsh.snapshot_list(vm_name)
        if snapshot_list:
            raise exceptions.TestFail("Snapshot not deleted: %s", snapshot_list)
    except Exception as detail:
        raise exceptions.TestFail("exception happens: %s", detail)
    finally:
        logging.debug("Start to clean up env...")
        vmxml_backup.sync()
        if pool_ins and pool_ins.pool_exists(pool_name):
            virsh.pool_destroy(pool_name)
        for new_vhba in new_vhbas:
            virsh.nodedev_destroy(new_vhba)
        utils_npiv.restart_multipathd()
        if old_mpath_conf:
            utils_npiv.prepare_multipath_conf(conf_path=mpath_conf_path,
                                              conf_content=old_mpath_conf,
                                              replace_existing=True)
        if not original_mpath_conf_exist and os.path.exists(mpath_conf_path):
            os.remove(mpath_conf_path)
