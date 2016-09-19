import logging
import time
from avocado.core import exceptions
from virttest import virsh
from virttest import libvirt_storage
from virttest import libvirt_xml
from virttest.utils_test import libvirt as utlv
from npiv import npiv_nodedev_create_destroy as nodedev
from avocado.utils import process


def mount_and_dd(session, mount_disk):
    """
    Mount given disk and perform a dd operation on it
    """
    output = session.cmd_status_output('mount /dev/%s /mnt' % mount_disk)
    logging.debug("%s", output[1])
    output = session.cmd_status_output(
        'dd if=/dev/zero of=/mnt/testfile bs=4k', timeout=120)
    logging.debug("dd output: %s", output[1])
    output = session.cmd_status_output('mount')
    logging.debug("Mount output: %s", output[1])
    if '/mnt' in output[1]:
        logging.debug("Mount Successful")


def run(test, params, env):
    """
    Test command: virsh pool-define-as; pool-build; pool-start; vol-create-as;
    vol-list; attach-device; login; mount and dd; reboot; check persistence;
    detach-device; pool-destroy; pool-undefine; clear lv,vg and pv;

    Create a libvirt npiv pool from a device mapper device and create a volume
    out of the newly created pool and attach it to a guest, mount it, reboot
    and check persistence after reboot.

    Pre-requisite :
    Host should have a multipath mapper device

    """
    pool_name = params.get("pool_create_name", "virt_test_pool_tmp")
    pool_type = params.get("pool_type", "dir")
    source_dev = params.get("pool_source_dev", "/dev/mapper/mpatha")
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

    if not vm.is_alive():
        vm.start()
    pool_extra_args = ""
    if source_dev:
        pool_extra_args = ' --source-dev %s' % source_dev
    pool_ins = libvirt_storage.StoragePool()
    if pool_ins.pool_exists(pool_name):
        raise exceptions.TestFail("Pool %s already exist" % pool_name)
    online_hbas_list = nodedev.find_hbas("hba")
    # if no online hba cards on host, mark case failed
    if not online_hbas_list:
        raise exceptions.TestSkipError("Host doesn't have online hba cards")
    try:
        cmd_result = virsh.pool_define_as(pool_name, pool_type, pool_target, pool_extra_args, ignore_status=True,
                                          debug=True)
        err = cmd_result.stderr.strip()
        status = cmd_result.exit_status
        if status:
            raise exceptions.TestFail(err)
        else:
            logging.info("Successfully define pool: %s", pool_name)

        cmd_result = virsh.pool_build(pool_name)
        err = cmd_result.stderr.strip()
        status = cmd_result.exit_status
        if status:
            raise exceptions.TestFail(err)
        else:
            logging.info("Successfully built pool: %s", pool_name)

        cmd_result = virsh.pool_start(pool_name)
        err = cmd_result.stderr.strip()
        status = cmd_result.exit_status
        if status:
            raise exceptions.TestFail(err)
        else:
            logging.info("Successfully start pool: %s", pool_name)

        utlv.check_actived_pool(pool_name)
        pool_detail = libvirt_xml.PoolXML.get_pool_details(pool_name)
        logging.debug("Pool detail: %s", pool_detail)

        cmd_result = virsh.vol_create_as(
            volume_name, pool_name, volume_capacity, allocation, frmt, "", debug=True)
        err = cmd_result.stderr.strip()
        status = cmd_result.exit_status
        if status:
            raise exceptions.TestFail(err)
        else:
            logging.info(
                "Successfully created volume out of pool: %s", pool_name)

        # Time for pool to be listed
        time.sleep(5)

        vol_list = utlv.get_vol_list(pool_name)
        logging.debug('Volume list %s', vol_list)
        for unit in vol_list:
            test_unit = vol_list[unit]
            logging.debug(unit)

        disk_params = {'type_name': "file", 'target_dev': target_device,
                       'target_bus': "virtio", 'source_file': test_unit,
                       'driver_name': "qemu", 'driver_type': "raw"}
        disk_xml = utlv.create_disk_xml(disk_params)
        session = vm.wait_for_login()

        attach_success = virsh.attach_device(
            vm_name, disk_xml, debug=True).exit_status

        if attach_success:
            raise exceptions.TestFail(
                "Failed to attach disk %s" % disk_xml)
        else:
            logging.debug('Device attached successfully')

        # Time for attached disk to display in guest
        time.sleep(10)

        output = session.cmd_status_output('lsblk')
        logging.debug("%s", output[1])
        mount_disk = None
        for line in output[1].splitlines():
            if line.startswith('vd'):
                mount_disk = line.split(' ')[0]

        session.cmd_status_output('mkfs.ext4 /dev/%s' % mount_disk)
        if mount_disk:
            logging.info("%s", mount_disk)
            mount_and_dd(session, mount_disk)

        virsh.reboot(vm_name, debug=True)

        session = vm.wait_for_login()
        output = session.cmd_status_output('mount')
        logging.debug("Mount output: %s", output[1])
        if '/mnt' in output[1]:
            logging.debug("Mount Successful accross reboot")

        status = virsh.detach_device(vm_name, disk_xml,
                                     debug=True).exit_status
        if status:
            raise exceptions.TestFail("Disk detach unsuccessful")
        else:
            logging.debug("Disk detach successful")

    finally:
        vm.destroy(gracefully=False)
        logging.debug('Destroying pool %s', pool_name)
        virsh.pool_destroy(pool_name)
        logging.debug('Undefining pool %s', pool_name)
        virsh.pool_undefine(pool_name)
        process.system('lvremove %s' % test_unit, verbose=True)
        process.system('vgremove %s' % pool_name, verbose=True)
        process.system('pvremove %s' % source_dev, verbose=True)
