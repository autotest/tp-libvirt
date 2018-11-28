import os
import re
import logging
import threading
import time

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import utils_npiv
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import disk
from virttest.utils_test import libvirt as utlv


_TIMEOUT = 5
_MPATH_DIR = "/dev/mapper/"
_BYPATH_DIR = "/dev/disk/by-path/"
_VM_FILE_PATH = "/tmp/test.txt"
_REPEAT = 10


def convert_img_to_dev(test, src_fmt, dest_fmt, img_src, blk_dev):
    """
    Use qemu-img convert to convert a source img to dest img

    :params fmt: Format of the output img, default to be qcow2
    :params img_src: Source img
    :params blk_dev: Destination img, here is a block device
    """
    cmd = ("qemu-img convert -f %s -O %s %s %s" %
           (src_fmt, dest_fmt, img_src, blk_dev))
    try:
        result = process.run(cmd, shell=True)
    except process.cmdError as detail:
        test.fail("Failed to convert img with exception %s" % detail)


def create_file_in_vm(vm, file_name, content, repeat):
    """
    Echo the <content> to <file_name> repeatedly for <repeat> times in vm.
    And between each echo, sleep for _TIMEOUT seconds, to make sure the
    process is long enough for concurrent test.
    """
    client_session = vm.wait_for_login()
    cmd_echo = "echo '%s' >> %s" % (content, file_name)
    for repeat_index in range(int(repeat)):
        logging.debug("Round %s of echo on vm: %s", repeat_index, vm.name)
        time.sleep(_TIMEOUT)
        try:
            status, output = client_session.cmd_status_output(cmd_echo)
        except process.cmdError as detail:
            logging.error("Fail to echo '%s' to '%s' in vm with"
                          " exception %s", content, file_name, detail)
            client_session = vm.wait_for_login()
            continue
    client_session.cmd_status_output("sync")
    client_session.close()


def check_file_in_vm(client_session, file_name, content, repeat):
    """
    Check <file_name>'s content in vm, return True if pass
    """
    test_cmd = "cat %s" % _VM_FILE_PATH
    status, output = client_session.cmd_status_output(test_cmd)
    logging.debug("%s", output)
    cmd = "cat %s | grep %s | wc -l" % (file_name, content)
    status, output = client_session.cmd_status_output(cmd)
    logging.debug("status = '%s', output = '%s'", status, output)
    logging.debug("output = %s, repeat = %s", output, repeat)
    return int(output) == int(repeat)


def prepare_disk_obj(disk_type, disk_device, driver_name, driver_type,
                     path_to_blk, device_target, target_bus):
    """
    Prepare and return disk xml object
    """
    disk_params = {'type_name': disk_type,
                   'device': disk_device,
                   'driver_name': driver_name,
                   'driver_type': driver_type,
                   'source_file': path_to_blk,
                   'target_dev': device_target,
                   'target_bus': target_bus
                   }
    vd_xml = utlv.create_disk_xml(disk_params)
    vd_xml_str = open(vd_xml).read()
    disk_obj = disk.Disk()
    disk_obj.xml = vd_xml_str
    return disk_obj


def replace_vm_first_vd(vm_name, disk_obj):
    """
    Replace vm's first virtual disk with virtual disk xml object specified
    by <disk_obj>. First, remove all virtual disks of a vm, then
    append a new virtual disk xml object.
    """
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    devices = vmxml.get_devices()
    disks = vmxml.get_devices(device_type="disk")
    for dev in disks:
        devices.remove(dev)
    devices.append(disk_obj)
    vmxml.set_devices(devices)
    logging.debug("vm's xml after replace vda is: %s", vmxml)
    vmxml.sync()


def get_blks_by_scsi(test, scsi_bus, blk_prefix="sd"):
    """
    Find the scsi block devices under /dev/, with specific scsi bus number

    :params scs_bus: the scsi bus number
    :params blk_prefix: such as "sd", "vd"
    :return: the block devices' name list
    """
    blk_names = []
    cmd = "multipath -ll | grep '\- %s:' | grep 'ready running' |"
    cmd += "awk '{FS=\" \"}{for (f=1; f<=NF; f+=1)"
    cmd += "{if ($f ~ /%s/) {print $f}}}'"
    cmd %= (scsi_bus, blk_prefix)
    try:
        result = process.run(cmd, shell=True)
        logging.debug("multipath result: %s", result.stdout_text.strip())
    except process.cmdError as detail:
        test.error("Error happend for multipath: %s" % detail)
    blk_names = result.stdout_text.strip().splitlines()
    return blk_names


def get_symbols_by_blk(test, blkdev, method="by-path"):
    """
    Find the lun device name under /dev/disk/by-path for specified
    block device.

    :params blkdev: the name of the block device, such as sda, sdb.
    :params method: find under which folder under /dev/disk, should
     be one of: by-path, by-id, by-uuid.
    :return: the blkdev's sl name under /dev/disk/[by-id|by-uuid|by-path]
    """
    symbolic_links = []
    dir_path = os.path.join("/dev/disk/", method)
    if not os.path.exists(dir_path):
        test.fail("Dir path %s does not exist!" % dir_path)
    logging.debug("dir_path=%s, blkdev=%s", dir_path, blkdev)
    try:
        cmd = "ls -al %s | grep %s | grep -v %s\[1-9\] |"
        cmd += "awk '{FS=\" \"} {for (f=1; f<=NF; f+=1) "
        cmd += "{if ($f ~ /pci/){print $f}}}'"
        cmd %= (dir_path, blkdev, blkdev)
        result = process.run(cmd, shell=True)
    except process.cmdError as detail:
        test.error("cmd wrong with error %s" % detail)
    symbolic_links = result.stdout_text.strip().splitlines()
    return symbolic_links


def run(test, params, env):
    vd_formats = []
    disk_devices = []
    driver_names = []
    driver_types = []
    device_targets = []
    target_buses = []
    wwnns = []
    wwpns = []

    vm_names = params.get("vms", "avocado-vt-vm1 avocado-vt-vm2").split()
    fc_host_dir = params.get("fc_host_dir", "/sys/class/fc_host")
    vm0_disk_type = params.get("vm0_disk_type", "block")
    vm1_disk_type = params.get("vm1_disk_type", "block")
    vm0_vd_format = params.get("vm0_vd_format", "by_path")
    vm1_vd_format = params.get("vm1_vd_foramt", "by_path")
    vm0_disk_device = vm1_disk_device = params.get("disk_device", "disk")
    vm0_driver_name = vm1_driver_name = params.get("driver_name", "qemu")
    vm0_driver_type = vm1_driver_type = params.get("driver_type", "qcow2")
    vm0_device_target = vm1_device_target = params.get("device_target", "vda")
    vm0_target_bus = vm1_target_bus = params.get("target_bus", "virtio")
    vm0_wwnn = params.get("vm0_wwnn", "ENTER.WWNN.FOR.VM0")
    vm0_wwpn = params.get("vm0_wwpn", "ENTER.WWPN.FOR.VM0")
    vm1_wwnn = params.get("vm1_wwnn", "ENTER.WWNN.FOR.VM1")
    vm1_wwpn = params.get("vm1_wwpn", "ENTER.WWPN.FOR.VM1")

    disk_types = [vm0_disk_type, vm1_disk_type]
    vd_formats = [vm0_vd_format, vm1_vd_format]
    disk_devices = [vm0_disk_device, vm1_disk_device]
    driver_names = [vm0_driver_name, vm1_driver_name]
    driver_types = [vm0_driver_type, vm1_driver_type]
    device_targets = [vm0_device_target, vm1_device_target]
    target_buses = [vm0_target_bus, vm1_target_bus]
    wwnns = [vm0_wwnn, vm1_wwnn]
    wwpns = [vm0_wwpn, vm1_wwpn]
    old_mpath_conf = ""
    mpath_conf_path = "/etc/multipath.conf"
    original_mpath_conf_exist = os.path.exists(mpath_conf_path)

    new_vhbas = []
    path_to_blks = []
    vmxml_backups = []
    vms = []

    try:
        online_hbas = utils_npiv.find_hbas("hba")
        if not online_hbas:
            test.cancel("There is no online hba cards.")
        old_mpath_conf = utils_npiv.prepare_multipath_conf(conf_path=mpath_conf_path,
                                                           replace_existing=True)
        first_online_hba = online_hbas[0]
        if len(vm_names) != 2:
            test.cancel("This test needs exactly 2 vms.")
        for vm_index in range(len(vm_names)):
            logging.debug("prepare vm %s", vm_names[vm_index])
            vm = env.get_vm(vm_names[vm_index])
            vms.append(vm)
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_names[vm_index])
            vmxml_backup = vmxml.copy()
            vmxml_backups.append(vmxml_backup)
            old_vhbas = utils_npiv.find_hbas("vhba")
            old_mpath_devs = utils_npiv.find_mpath_devs()
            new_vhba = utils_npiv.nodedev_create_from_xml(
                    {"nodedev_parent": first_online_hba,
                     "scsi_wwnn": wwnns[vm_index],
                     "scsi_wwpn": wwpns[vm_index]})
            utils_misc.wait_for(
                    lambda: utils_npiv.is_vhbas_added(old_vhbas),
                    timeout=_TIMEOUT*2)
            if not new_vhba:
                test.fail("vHBA not sucessfully generated.")
            new_vhbas.append(new_vhba)
            if vd_formats[vm_index] == "mpath":
                utils_misc.wait_for(
                        lambda: utils_npiv.is_mpath_devs_added(old_mpath_devs),
                        timeout=_TIMEOUT*5)
                if not utils_npiv.is_mpath_devs_added(old_mpath_devs):
                    test.fail("mpath dev not generated.")
                cur_mpath_devs = utils_npiv.find_mpath_devs()
                new_mpath_devs = list(set(cur_mpath_devs).difference(
                    set(old_mpath_devs)))
                logging.debug("The newly added mpath dev is: %s",
                              new_mpath_devs)
                path_to_blk = os.path.join(_MPATH_DIR, new_mpath_devs[0])
            elif vd_formats[vm_index] == "by_path":
                new_vhba_scsibus = re.sub("\D", "", new_vhba)
                utils_misc.wait_for(lambda: get_blks_by_scsi(test, new_vhba_scsibus),
                                    timeout=_TIMEOUT)
                new_blks = get_blks_by_scsi(test, new_vhba_scsibus)
                if not new_blks:
                    test.fail("blk dev not found with scsi_%s" % new_vhba_scsibus)
                first_blk_dev = new_blks[0]
                utils_misc.wait_for(
                        lambda: get_symbols_by_blk(test, first_blk_dev),
                        timeout=_TIMEOUT)
                lun_sl = get_symbols_by_blk(test, first_blk_dev)
                if not lun_sl:
                    test.fail("lun symbolic links not found in "
                              "/dev/disk/by-path/ for %s" %
                              first_blk_dev)
                lun_dev = lun_sl[0]
                path_to_blk = os.path.join(_BYPATH_DIR, lun_dev)
            path_to_blks.append(path_to_blk)
            img_src = vm.get_first_disk_devices()['source']
            img_info = utils_misc.get_image_info(img_src)
            src_fmt = img_info["format"]
            dest_fmt = "qcow2"
            convert_img_to_dev(test, src_fmt, dest_fmt, img_src, path_to_blk)
            disk_obj = prepare_disk_obj(disk_types[vm_index], disk_devices[vm_index],
                                        driver_names[vm_index], driver_types[vm_index],
                                        path_to_blk, device_targets[vm_index],
                                        target_buses[vm_index])
            replace_vm_first_vd(vm_names[vm_index], disk_obj)
            if vm.is_dead():
                logging.debug("Start vm %s with updated vda", vm_names[vm_index])
                vm.start()

        # concurrently create file in vm with threads
        create_file_in_vm_threads = []
        for vm in vms:
            cli_t = threading.Thread(target=create_file_in_vm,
                                     args=(vm, _VM_FILE_PATH, vm.name, _REPEAT,)
                                     )
            logging.debug("Start creating file in vm: %s", vm.name)
            create_file_in_vm_threads.append(cli_t)
            cli_t.start()
        for thrd in create_file_in_vm_threads:
            thrd.join()

        # reboot vm and check if previously create file still exist with
        # correct content
        for vm in vms:
            session = vm.wait_for_login()
            session.cmd_status_output("sync")
            if vm.is_alive:
                vm.destroy(gracefully=True)
            else:
                test.fail("%s is not running" % vm.name)
            vm.start()
            session = vm.wait_for_login()
            if check_file_in_vm(session, _VM_FILE_PATH, vm.name, _REPEAT):
                logging.debug("file exists after reboot with correct content")
            else:
                test.fail("Failed to check the test file in vm")
            session.close()
    except Exception as detail:
        test.fail("Test failed with exception: %s" % detail)
    finally:
        logging.debug("Start to clean up env...")
        for vmxml_backup in vmxml_backups:
            vmxml_backup.sync()
        for new_vhba in new_vhbas:
            virsh.nodedev_destroy(new_vhba)
        process.system('service multipathd restart', verbose=True)
        if old_mpath_conf:
            utils_npiv.prepare_multipath_conf(conf_path=mpath_conf_path,
                                              conf_content=old_mpath_conf,
                                              replace_existing=True)
        if not original_mpath_conf_exist and os.path.exists(mpath_conf_path):
            os.remove(mpath_conf_path)
