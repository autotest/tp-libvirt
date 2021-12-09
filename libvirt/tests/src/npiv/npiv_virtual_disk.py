import os
import re
import logging
from shutil import copyfile

from avocado.core import exceptions
from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import data_dir
from virttest import utils_npiv
from virttest import libvirt_vm as lib_vm
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt as utlv


_TIMEOUT = 5


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
    logging.debug("dir_path=%s, blkdev=%s" % (dir_path, blkdev))
    try:
        cmd = "ls -al %s | grep %s | grep -v %s\[1-9\] |\
               awk '{FS=\" \"} {for (f=1; f<=NF; f+=1) \
               {if ($f ~ /pci/){print $f}}}'" % (dir_path, blkdev, blkdev)
        result = process.run(cmd, shell=True)
    except Exception as e:
        raise exceptions.TestError(str(e))
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
    cmd = "multipath -ll | grep '\- %s:' | grep 'ready running' |\
           awk '{FS=\" \"}{for (f=1; f<=NF; f+=1)\
           {if ($f ~ /%s/) {print $f}}}'" % (scsi_bus, blk_prefix)
    try:
        result = process.run(cmd, shell=True)
    except Exception as e:
        raise exceptions.TestError(str(e))
    blk_names = result.stdout_text.strip().splitlines()
    return blk_names


def check_vm_disk(session, blkdev, readonly="no"):
    """
    Check the disk in vm, by mount it and dd data to it

    :params session: a vm session
    :params blkdev: the block device
    :params readonly: if the block device is attached in readonly mode
    :return: True if check successfully, False if not
    """
    status, output = session.cmd_status_output('mount %s /mnt' % blkdev)
    logging.debug(output)
    if readonly == "yes":
        if (("read-only" not in output.lower()) and
                ("readonly" not in output.lower())):
            logging.error("A readonly virtual disk mounted as "
                          "a non-readonly disk.")
            return False
    status, output = session.cmd_status_output(
            'dd if=/dev/zero of=/mnt/testfile bs=4k count=8000',
            timeout=_TIMEOUT*100)
    logging.debug(output)
    if readonly == "yes":
        if not status:
            logging.error("A readonly disk can be written!")
            return False
    elif readonly == "no":
        if status:
            logging.error("A w/r disk cannot be written!")
            return False
    status, output = session.cmd_status_output('mount')
    if '/mnt' in output:
        logging.debug("Mount And dd returned expected result.")
        return True
    logging.error("Mount and dd %s in vm with unexpected result.", blkdev)
    return False


def run(test, params, env):
    """
    1. prepare a vHBA
    2. find the nodedev's lun name
    3. prepare the lun dev's xml
    4. start vm
    5. attach disk xml to vm
    6. login vm and check the disk
    7. detach the virtual disk
    8. check the blkdev gone
    9. cleanup env.
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    wwpn = params.get("wwpn", "WWPN_EXAMPLE")
    wwnn = params.get("wwnn", "WWNN_EXAMPLE")
    disk_device = params.get("disk_device", "disk")
    device_type = params.get("device_type", "block")
    device_target = params.get("device_target", "vdb")
    lun_dir_method = params.get("lun_dir_method", "by-path")
    driver_name = params.get("driver_name", "qemu")
    driver_type = params.get("driver_type", "raw")
    target_bus = params.get("target_bus", "virtio")
    readonly = params.get("readonly", "no")
    new_vhbas = []
    blk_dev = ""
    lun_dev = ""
    lun_dev_path = ""
    lun_sl = []
    new_disk = ""
    old_mpath_conf = ""
    mpath_conf_path = "/etc/multipath.conf"
    original_mpath_conf_exist = os.path.exists(mpath_conf_path)
    vm = env.get_vm(vm_name)
    try:
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()
        old_disk_count = vmxml.get_disk_count(vm_name)
        # Prepare vHBA
        online_hbas = utils_npiv.find_hbas("hba")
        old_vhbas = utils_npiv.find_hbas("vhba")
        if not online_hbas:
            raise exceptions.TestSkipError("Host doesn't have online hba!")
        old_mpath_conf = utils_npiv.prepare_multipath_conf(conf_path=mpath_conf_path,
                                                           replace_existing=True)
        first_online_hba = online_hbas[0]
        new_vhba = utils_npiv.nodedev_create_from_xml(
                {"nodedev_parent": first_online_hba,
                 "scsi_wwnn": wwnn,
                 "scsi_wwpn": wwpn})
        utils_misc.wait_for(lambda: utils_npiv.is_vhbas_added(old_vhbas),
                            timeout=_TIMEOUT)
        if not utils_npiv.is_vhbas_added(old_vhbas):
            raise exceptions.TestFail("vHBA is not successfully created.")
        new_vhbas.append(new_vhba)
        new_vhba_scsibus = re.sub("\D", "", new_vhba)
        # Get the new block device generated by the new vHBA
        utils_misc.wait_for(lambda: get_blks_by_scsi(new_vhba_scsibus),
                            timeout=_TIMEOUT)
        blk_devs = get_blks_by_scsi(new_vhba_scsibus)
        if not blk_devs:
            raise exceptions.TestFail("block device not found with scsi_%s",
                                      new_vhba_scsibus)
        first_blk_dev = blk_devs[0]
        # Get the symbolic link of the device in /dev/disk/by-[path|uuid|id]
        logging.debug("first_blk_dev = %s, lun_dir_method = %s"
                      % (first_blk_dev, lun_dir_method))
        utils_misc.wait_for(
            lambda: get_symbols_by_blk(first_blk_dev, lun_dir_method),
            timeout=_TIMEOUT)
        lun_sl = get_symbols_by_blk(first_blk_dev, lun_dir_method)
        if not lun_sl:
            raise exceptions.TestFail("lun symbolic links not found under "
                                      "/dev/disk/%s/ for block device %s." %
                                      (lun_dir_method, blk_dev))
        lun_dev = lun_sl[0]
        lun_dev_path = "/dev/disk/" + lun_dir_method + "/" + lun_dev
        # Prepare xml of virtual disk
        disk_params = {'type_name': device_type, 'device': disk_device,
                       'driver_name': driver_name, 'driver_type': driver_type,
                       'source_file': lun_dev_path,
                       'target_dev': device_target, 'target_bus': target_bus,
                       'readonly': readonly}
        disk_xml = os.path.join(data_dir.get_tmp_dir(), 'disk_xml.xml')
        lun_disk_xml = utlv.create_disk_xml(disk_params)
        copyfile(lun_disk_xml, disk_xml)
        if not vm.is_alive():
            vm.start()
        session = vm.wait_for_login()
        libvirt_vm = lib_vm.VM(vm_name, vm.params, vm.root_dir,
                               vm.address_cache)
        old_disks = libvirt_vm.get_disks()
        # Attach disk
        dev_attach_status = virsh.attach_device(
                    vm_name, disk_xml, debug=True)
        utlv.check_exit_status(dev_attach_status)

        cur_disk_count = vmxml.get_disk_count(vm_name)
        cur_disks = libvirt_vm.get_disks()
        if cur_disk_count <= old_disk_count:
            raise exceptions.TestFail(
                    "Failed to attach disk: %s" % lun_disk_xml)
        new_disk = "".join(list(set(old_disks) ^ set(cur_disks)))
        logging.debug("Attached device in vm:%s", new_disk)
        # Check disk in VM
        output = session.cmd_status_output('mkfs.ext4 -F %s' % new_disk)
        logging.debug("mkfs.ext4 the disk in vm, result: %s", output[1])
        if not check_vm_disk(session, new_disk, readonly):
            raise exceptions.TestFail("Failed check the disk in vm.")
        session.cmd_status_output('umount %s' % new_disk)
        # Detach disk
        dev_detach_status = virsh.detach_device(vm_name, disk_xml, debug=True)
        utlv.check_exit_status(dev_detach_status)
        cur_disks = libvirt_vm.get_disks()
        if cur_disks != old_disks:
            raise exceptions.TestFail("Detach disk failed.")
        session.close()

    finally:
        utils_npiv.vhbas_cleanup(new_vhbas)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        vmxml_backup.sync()
        process.system('service multipathd restart', verbose=True)
        if old_mpath_conf:
            utils_npiv.prepare_multipath_conf(conf_path=mpath_conf_path,
                                              conf_content=old_mpath_conf,
                                              replace_existing=True)
        if not original_mpath_conf_exist and os.path.exists(mpath_conf_path):
            os.remove(mpath_conf_path)
