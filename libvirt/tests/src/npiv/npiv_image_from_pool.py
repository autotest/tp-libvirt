import logging
import os
from avocado.core import exceptions
from avocado.utils import process
from virttest import virsh
from virttest import libvirt_storage
from virttest import libvirt_xml
from virttest import utils_misc
from virttest import libvirt_vm as lib_vm
from virttest.utils_test import libvirt as utlv
from virttest import utils_npiv as nodedev


def mount_and_dd(session, mount_disk):
    """
    Mount given disk and perform a dd operation on it
    """
    output = session.cmd_status_output('mount %s /mnt' % mount_disk)
    logging.debug("%s", output[1])
    output = session.cmd_status_output(
        'dd if=/dev/zero of=/mnt/testfile bs=4k', timeout=120)
    logging.debug("dd output: %s", output[1])
    output = session.cmd_status_output('mount')
    logging.debug("Mount output: %s", output[1])
    if '/mnt' in output[1]:
        logging.debug("Mount Successful")
        return True
    return False


def run(test, params, env):
    """
    Test command: virsh pool-define-as; pool-build; pool-start; vol-create-as;
    vol-list; attach-device; login; mount and dd; reboot; check persistence;
    detach-device; pool-destroy; pool-undefine; clear lv,vg and pv;
    Create a libvirt npiv pool from a vHBA's device mapper device and create
    a volume out of the newly created pool and attach it to a guest, mount it,
    reboot and check persistence after reboot.

    Pre-requisite :
    Host should have a vHBA associated with a mpath device
    """

    pool_name = params.get("pool_create_name", "virt_test_pool_tmp")
    pool_type = params.get("pool_type", "dir")
    scsi_wwpn = params.get("scsi_wwpn", "WWPN_EXAMPLE")
    scsi_wwnn = params.get("scsi_wwnn", "WWNN_EXAMPLE")
    pool_target = params.get("pool_target", "pool_target")
    target_device = params.get("disk_target_dev", "vda")
    volume_name = params.get("volume_name", "imagefrommapper.qcow2")
    volume_capacity = params.get("volume_capacity", '1G')
    allocation = params.get("allocation", '1G')
    frmt = params.get("volume_format", 'qcow2')
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    mount_disk = None
    test_unit = None

    if 'EXAMPLE' in scsi_wwnn or 'EXAMPLE' in scsi_wwpn:
        raise exceptions.TestSkipError("Please provide proper WWPN/WWNN")

    if not vm.is_alive():
        vm.start()
    pool_extra_args = ""
    libvirt_vm = lib_vm.VM(vm_name, vm.params, vm.root_dir,
                           vm.address_cache)
    process.run("service multipathd restart", shell=True)
    online_hbas_list = nodedev.find_hbas("hba")
    first_online_hba = online_hbas_list[0]
    old_mpath_devs = nodedev.find_mpath_devs()
    logging.debug("the old mpath devs are: %s" % old_mpath_devs)
    new_vhbas = nodedev.nodedev_create_from_xml(
        {"nodedev_parent": first_online_hba,
         "scsi_wwnn": scsi_wwnn,
         "scsi_wwpn": scsi_wwpn})
    logging.info("Newly created vHBA %s" % new_vhbas)
    process.run("service multipathd restart", shell=True)

    utils_misc.wait_for(
        lambda: nodedev.is_mpath_devs_added(old_mpath_devs), timeout=5)

    cur_mpath_devs = nodedev.find_mpath_devs()
    logging.debug("the current mpath devs are: %s" % cur_mpath_devs)
    new_mpath_devs = list(set(cur_mpath_devs).difference(
        set(old_mpath_devs)))

    logging.debug("newly added mpath devs are: %s" % new_mpath_devs)
    if not new_mpath_devs:
        raise exceptions.TestFail("No newly added mpath devices found, \
                please check your FC settings")
    source_dev = os.path.join('/dev/mapper/', new_mpath_devs[0])
    logging.debug("We are going to use \"%s\" as our source device"
                  " to create a logical pool" % source_dev)

    cmd = "parted %s mklabel msdos -s" % source_dev
    cmd_result = process.run(cmd, shell=True)
    utlv.check_exit_status(cmd_result)

    if source_dev:
        pool_extra_args = ' --source-dev %s' % source_dev
    else:
        raise exceptions.TestFail(
            "The vHBA %s does not have any associated mpath device" % new_vhbas)

    pool_ins = libvirt_storage.StoragePool()
    if pool_ins.pool_exists(pool_name):
        raise exceptions.TestFail("Pool %s already exist" % pool_name)
    # if no online hba cards on host, mark case failed
    if not online_hbas_list:
        raise exceptions.TestSkipError("Host doesn't have online hba cards")
    try:
        cmd_result = virsh.pool_define_as(
            pool_name, pool_type, pool_target, pool_extra_args, ignore_status=True,
            debug=True)
        utlv.check_exit_status(cmd_result)

        cmd_result = virsh.pool_build(pool_name)
        utlv.check_exit_status(cmd_result)

        cmd_result = virsh.pool_start(pool_name)
        utlv.check_exit_status(cmd_result)

        utlv.check_actived_pool(pool_name)
        pool_detail = libvirt_xml.PoolXML.get_pool_details(pool_name)
        logging.debug("Pool detail: %s", pool_detail)

        cmd_result = virsh.vol_create_as(
            volume_name, pool_name, volume_capacity, allocation, frmt, "", debug=True)
        utlv.check_exit_status(cmd_result)

        vol_list = utlv.get_vol_list(pool_name, timeout=10)
        logging.debug('Volume list %s', vol_list)
        for unit in vol_list:
            test_unit = vol_list[unit]
            logging.debug(unit)

        disk_params = {'type_name': "file", 'target_dev': target_device,
                       'target_bus': "virtio", 'source_file': test_unit,
                       'driver_name': "qemu", 'driver_type': "raw"}
        disk_xml = utlv.create_disk_xml(disk_params)
        session = vm.wait_for_login()

        bf_disks = libvirt_vm.get_disks()

        attach_success = virsh.attach_device(
            vm_name, disk_xml, debug=True)

        utlv.check_exit_status(attach_success)

        logging.debug("Disks before attach: %s", bf_disks)

        af_disks = libvirt_vm.get_disks()

        logging.debug("Disks after attach: %s", af_disks)

        mount_disk = "".join(list(set(bf_disks) ^ set(af_disks)))
        if not mount_disk:
            raise exceptions.TestFail("Can not get attached device in vm.")
        logging.debug("Attached device in vm:%s", mount_disk)

        output = session.cmd_status_output('lsblk', timeout=15)
        logging.debug("%s", output[1])

        session.cmd_status_output('mkfs.ext4 %s' % mount_disk)
        if mount_disk:
            logging.info("%s", mount_disk)
            mount_success = mount_and_dd(session, mount_disk)
            if not mount_success:
                raise exceptions.TestFail("Can not find mounted device")
        session.close()

        virsh.reboot(vm_name, debug=True)

        session = vm.wait_for_login()
        output = session.cmd_status_output('mount')
        logging.debug("Mount output: %s", output[1])
        if '/mnt' in output[1]:
            logging.debug("Mount Successful accross reboot")
        session.close()

        status = virsh.detach_device(vm_name, disk_xml,
                                     debug=True)
        utlv.check_exit_status(status)

    finally:
        vm.destroy(gracefully=False)
        logging.debug('Destroying pool %s', pool_name)
        virsh.pool_destroy(pool_name)
        logging.debug('Undefining pool %s', pool_name)
        virsh.pool_undefine(pool_name)
        if test_unit:
            process.system('lvremove -f %s' % test_unit, verbose=True)
            process.system('vgremove -f %s' % pool_name, verbose=True)
            process.system('pvremove -f %s' % source_dev, verbose=True)
        if new_vhbas:
            nodedev.vhbas_cleanup(new_vhbas.split())
        process.run("service multipathd restart", shell=True)
