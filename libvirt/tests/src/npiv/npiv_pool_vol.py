import os
import logging
from shutil import copyfile
from avocado.core import exceptions
from avocado.utils import process
from virttest import virsh
from virttest import libvirt_storage
from virttest import libvirt_xml
from virttest import data_dir
from virttest import libvirt_vm as lib_vm
from virttest import utils_misc
from virttest import utils_npiv
from virttest import virt_vm
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import disk
from virttest.utils_test import libvirt as utlv

_DELAY_TIME = 5


def mount_and_dd(session, mount_disk):
    """
    Mount and perform a dd operation on guest
    """
    output = session.cmd_status_output('mount %s /mnt' % mount_disk)
    logging.debug("mount: %s", output[1])
    output = session.cmd_status_output(
        'dd if=/dev/zero of=/mnt/testfile bs=4k count=8000',
        timeout=_DELAY_TIME*100)
    logging.debug("dd output: %s", output[1])
    output = session.cmd_status_output('mount')
    logging.debug("Mount output: %s", output[1])
    if '/mnt' in output[1]:
        logging.debug("Mount Successful")
        return True
    return False


def run(test, params, env):
    """
    Test command: virsh pool-define; pool-define-as; pool-start;
    vol-list pool; attach-device LUN to guest; mount the device;
    dd to the mounted device; unmount; pool-destroy; pool-undefine;

    Pre-requiste:
    Host needs to have a wwpn and wwnn of a vHBA which is zoned and mapped to
    SAN controller.
    """
    pool_xml_f = params.get("pool_create_xml_file", "/PATH/TO/POOL.XML")
    pool_name = params.get("pool_create_name", "virt_test_pool_tmp")
    pre_def_pool = params.get("pre_def_pool", "no")
    define_pool = params.get("define_pool", "no")
    define_pool_as = params.get("define_pool_as", "no")
    pool_create_as = params.get("pool_create_as", "no")
    need_pool_build = params.get("need_pool_build", "no")
    need_vol_create = params.get("need_vol_create", "no")
    pool_type = params.get("pool_type", "dir")
    source_format = params.get("pool_src_format", "")
    source_name = params.get("pool_source_name", "")
    source_path = params.get("pool_source_path", "/")
    pool_target = params.get("pool_target", "pool_target")
    pool_adapter_type = params.get("pool_adapter_type", "")
    pool_adapter_parent = params.get("pool_adapter_parent", "")
    target_device = params.get("disk_target_dev", "sdc")
    pool_wwnn = params.get("pool_wwnn", "POOL_WWNN_EXAMPLE")
    pool_wwpn = params.get("pool_wwpn", "POOL_WWPN_EXAMPLE")
    vhba_wwnn = params.get("vhba_wwnn", "VHBA_WWNN_EXAMPLE")
    vhba_wwpn = params.get("vhba_wwpn", "VHBA_WWPN_EXAMPLE")
    volume_name = params.get("volume_name", "imagefrommapper.qcow2")
    volume_capacity = params.get("volume_capacity", '1G')
    allocation = params.get("allocation", '1G')
    vol_format = params.get("volume_format", 'raw')
    attach_method = params.get("attach_method", "hot")
    test_unit = None
    mount_disk = None
    pool_kwargs = {}
    pool_extra_args = ""
    emulated_image = "emulated-image"
    disk_xml = ""
    new_vhbas = []
    source_dev = ""
    mpath_vol_path = ""
    old_mpath_conf = ""
    mpath_conf_path = "/etc/multipath.conf"
    original_mpath_conf_exist = os.path.exists(mpath_conf_path)

    if pool_type == "scsi":
        if ('EXAMPLE' in pool_wwnn) or ('EXAMPLE' in pool_wwpn):
            raise exceptions.TestSkipError(
                    "No wwpn and wwnn provided for npiv scsi pool.")
    if pool_type == "logical":
        if ('EXAMPLE' in vhba_wwnn) or ('EXAMPLE' in vhba_wwpn):
            raise exceptions.TestSkipError(
                    "No wwpn and wwnn provided for vhba.")
    online_hbas_list = utils_npiv.find_hbas("hba")
    logging.debug("The online hbas are: %s", online_hbas_list)
    old_mpath_conf = utils_npiv.prepare_multipath_conf(conf_path=mpath_conf_path,
                                                       replace_existing=True)
    if not online_hbas_list:
        raise exceptions.TestSkipError(
            "Host doesn't have online hba cards")
    old_vhbas = utils_npiv.find_hbas("vhba")

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()
    if not vm.is_alive():
        vm.start()
    libvirt_vm = lib_vm.VM(vm_name, vm.params, vm.root_dir,
                           vm.address_cache)
    pool_ins = libvirt_storage.StoragePool()
    if pool_ins.pool_exists(pool_name):
        raise exceptions.TestFail("Pool %s already exist" % pool_name)
    if pool_type == "scsi":
        if define_pool == "yes":
            if pool_adapter_parent == "":
                pool_adapter_parent = online_hbas_list[0]
            pool_kwargs = {'source_path': source_path,
                           'source_name': source_name,
                           'source_format': source_format,
                           'pool_adapter_type': pool_adapter_type,
                           'pool_adapter_parent': pool_adapter_parent,
                           'pool_wwnn': pool_wwnn,
                           'pool_wwpn': pool_wwpn}
    elif pool_type == "logical":
        if (not vhba_wwnn) or (not vhba_wwpn):
            raise exceptions.TestFail("No wwnn/wwpn provided to create vHBA.")
        old_mpath_devs = utils_npiv.find_mpath_devs()
        new_vhba = utils_npiv.nodedev_create_from_xml({
                "nodedev_parent": online_hbas_list[0],
                "scsi_wwnn": vhba_wwnn,
                "scsi_wwpn": vhba_wwpn})
        utils_misc.wait_for(
            lambda: utils_npiv.is_vhbas_added(old_vhbas), timeout=_DELAY_TIME*2)
        if not new_vhba:
            raise exceptions.TestFail("vHBA not sucessfully generated.")
        new_vhbas.append(new_vhba)
        utils_misc.wait_for(
            lambda: utils_npiv.is_mpath_devs_added(old_mpath_devs),
            timeout=_DELAY_TIME*5)
        if not utils_npiv.is_mpath_devs_added(old_mpath_devs):
            raise exceptions.TestFail("mpath dev not generated.")
        cur_mpath_devs = utils_npiv.find_mpath_devs()
        new_mpath_devs = list(set(cur_mpath_devs).difference(
            set(old_mpath_devs)))
        logging.debug("The newly added mpath dev is: %s", new_mpath_devs)
        source_dev = "/dev/mapper/" + new_mpath_devs[0]
        logging.debug("We are going to use \"%s\" as our source device"
                      " to create a logical pool", source_dev)
        try:
            cmd = "parted %s mklabel msdos -s" % source_dev
            cmd_result = process.run(cmd, shell=True)
        except Exception as e:
            raise exceptions.TestError("Error occurred when parted mklable")
        if define_pool_as == "yes":
            pool_extra_args = ""
            if source_dev:
                pool_extra_args = ' --source-dev %s' % source_dev
    elif pool_type == "mpath":
        if (not vhba_wwnn) or (not vhba_wwpn):
            raise exceptions.TestFail("No wwnn/wwpn provided to create vHBA.")
        old_mpath_devs = utils_npiv.find_mpath_devs()
        new_vhba = utils_npiv.nodedev_create_from_xml({
                "nodedev_parent": online_hbas_list[0],
                "scsi_wwnn": vhba_wwnn,
                "scsi_wwpn": vhba_wwpn})
        utils_misc.wait_for(
            lambda: utils_npiv.is_vhbas_added(old_vhbas), timeout=_DELAY_TIME*2)
        if not new_vhba:
            raise exceptions.TestFail("vHBA not sucessfully generated.")
        new_vhbas.append(new_vhba)
        utils_misc.wait_for(
            lambda: utils_npiv.is_mpath_devs_added(old_mpath_devs),
            timeout=_DELAY_TIME*2)
        if not utils_npiv.is_mpath_devs_added(old_mpath_devs):
            raise exceptions.TestFail("mpath dev not generated.")
        cur_mpath_devs = utils_npiv.find_mpath_devs()
        new_mpath_devs = list(set(cur_mpath_devs).difference(
            set(old_mpath_devs)))
        logging.debug("The newly added mpath dev is: %s", new_mpath_devs)
        mpath_vol_path = "/dev/mapper/" + new_mpath_devs[0]
        try:
            cmd = "parted %s mklabel msdos -s" % mpath_vol_path
            cmd_result = process.run(cmd, shell=True)
        except Exception as e:
            raise exceptions.TestError("Error occurred when parted mklable")
    if pre_def_pool == "yes":
        try:
            pvt = utlv.PoolVolumeTest(test, params)
            pvt.pre_pool(pool_name, pool_type,
                         pool_target, emulated_image,
                         **pool_kwargs)
            utils_misc.wait_for(
                    lambda: utils_npiv.is_vhbas_added(old_vhbas),
                    _DELAY_TIME*2)
            virsh.pool_dumpxml(pool_name, to_file=pool_xml_f)
            virsh.pool_destroy(pool_name)
        except Exception as e:
            pvt.cleanup_pool(pool_name, pool_type, pool_target,
                             emulated_image, **pool_kwargs)
            raise exceptions.TestError(
                "Error occurred when prepare pool xml:\n %s" % e)
        if os.path.exists(pool_xml_f):
            with open(pool_xml_f, 'r') as f:
                logging.debug("Create pool from file: %s", f.read())
    try:
        # define/create/start the pool
        if (pre_def_pool == "yes") and (define_pool == "yes"):
            pool_define_status = virsh.pool_define(pool_xml_f,
                                                   ignore_status=True,
                                                   debug=True)
            utlv.check_exit_status(pool_define_status)
        if define_pool_as == "yes":
            pool_define_as_status = virsh.pool_define_as(
                pool_name, pool_type,
                pool_target, pool_extra_args,
                ignore_status=True, debug=True
                )
            utlv.check_exit_status(pool_define_as_status)
        if pool_create_as == "yes":
            if pool_type != "scsi":
                raise exceptions.TestSkipError("pool-create-as only needs to "
                                               "be covered by scsi pool for "
                                               "NPIV test.")
            cmd = "virsh pool-create-as %s %s \
                   --adapter-wwnn %s --adapter-wwpn %s \
                   --adapter-parent %s --target %s"\
                   % (pool_name, pool_type, pool_wwnn, pool_wwpn,
                      online_hbas_list[0], pool_target)
            cmd_status = process.system(cmd, verbose=True)
            if cmd_status:
                raise exceptions.TestFail("pool-create-as scsi pool failed.")
        if need_pool_build == "yes":
            pool_build_status = virsh.pool_build(pool_name, "--overwrite")
            utlv.check_exit_status(pool_build_status)

        pool_ins = libvirt_storage.StoragePool()
        if not pool_ins.pool_exists(pool_name):
            raise exceptions.TestFail("define or create pool failed.")
        else:
            if not pool_ins.is_pool_active(pool_name):
                pool_start_status = virsh.pool_start(pool_name)
                utlv.check_exit_status(pool_start_status)
                utlv.check_actived_pool(pool_name)
                pool_detail = libvirt_xml.PoolXML.get_pool_details(pool_name)
                logging.debug("Pool detail: %s", pool_detail)

        # create vol if required
        if need_vol_create == "yes":
            vol_create_as_status = virsh.vol_create_as(
                    volume_name, pool_name,
                    volume_capacity, allocation,
                    vol_format, "", debug=True
                    )
            utlv.check_exit_status(vol_create_as_status)
        virsh.pool_refresh(pool_name)
        vol_list = utlv.get_vol_list(pool_name, vol_check=True,
                                     timeout=_DELAY_TIME*3)
        logging.debug('Volume list is: %s' % vol_list)

        # use test_unit to save the first vol in pool
        if pool_type == "mpath":
            cmd = "virsh vol-list %s | grep \"%s\" |\
                   awk '{FS=\" \"} {print $1}'" % (pool_name, mpath_vol_path)
            cmd_result = process.run(cmd, shell=True)
            status = cmd_result.exit_status
            output = cmd_result.stdout_text.strip()
            if cmd_result.exit_status:
                raise exceptions.TestFail("vol-list pool %s failed", pool_name)
            if not output:
                raise exceptions.TestFail("Newly added mpath dev not in pool.")
            test_unit = output
            logging.info(
                "Using %s to attach to a guest", test_unit)
        else:
            test_unit = list(vol_list.keys())[0]
            logging.info(
                "Using the first volume %s to attach to a guest", test_unit)

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        session = vm.wait_for_login()
        output = session.cmd_status_output('lsblk')
        logging.debug("%s", output[1])
        old_count = vmxml.get_disk_count(vm_name)
        bf_disks = libvirt_vm.get_disks()

        # prepare disk xml which will be hot/cold attached to vm
        disk_params = {'type_name': 'volume', 'target_dev': target_device,
                       'target_bus': 'virtio', 'source_pool': pool_name,
                       'source_volume': test_unit, 'driver_type': vol_format}
        disk_xml = os.path.join(data_dir.get_tmp_dir(), 'disk_xml.xml')
        lun_disk_xml = utlv.create_disk_xml(disk_params)
        copyfile(lun_disk_xml, disk_xml)
        disk_xml_str = open(lun_disk_xml).read()
        logging.debug("The disk xml is: %s", disk_xml_str)

        # hot attach disk xml to vm
        if attach_method == "hot":
            copyfile(lun_disk_xml, disk_xml)
            dev_attach_status = virsh.attach_device(vm_name, disk_xml,
                                                    debug=True)
            # Pool/vol virtual disk is not supported by mpath pool yet.
            if dev_attach_status.exit_status and pool_type == "mpath":
                raise exceptions.TestSkipError("mpath pool vol is not "
                                               "supported in virtual disk yet,"
                                               "the error message is: %s",
                                               dev_attach_status.stderr)
                session.close()
            utlv.check_exit_status(dev_attach_status)
        # cold attach disk xml to vm
        elif attach_method == "cold":
            if vm.is_alive():
                vm.destroy(gracefully=False)
            new_disk = disk.Disk()
            new_disk.xml = disk_xml_str
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            vmxml.devices = vmxml.devices.append(new_disk)
            vmxml.sync()
            logging.debug(vmxml)
            try:
                vm.start()
            except virt_vm.VMStartError as e:
                logging.debug(e)
                if pool_type == "mpath":
                    raise exceptions.TestSkipError("'mpath' pools for backing "
                                                   "'volume' disks isn't "
                                                   "supported for now")
                else:
                    raise exceptions.TestFail("Failed to start vm")
            session = vm.wait_for_login()
        else:
            pass

        # checking attached disk in vm
        logging.info("Checking disk availability in domain")
        if not vmxml.get_disk_count(vm_name):
            raise exceptions.TestFail("No disk in domain %s." % vm_name)
        new_count = vmxml.get_disk_count(vm_name)

        if new_count <= old_count:
            raise exceptions.TestFail(
                "Failed to attach disk %s" % lun_disk_xml)
        logging.debug("Disks before attach: %s", bf_disks)

        af_disks = libvirt_vm.get_disks()
        logging.debug("Disks after attach: %s", af_disks)

        mount_disk = "".join(list(set(bf_disks) ^ set(af_disks)))
        if not mount_disk:
            raise exceptions.TestFail("Can not get attached device in vm.")
        logging.debug("Attached device in vm:%s", mount_disk)

        logging.debug("Creating file system for %s", mount_disk)
        output = session.cmd_status_output(
            'echo yes | mkfs.ext4 %s' % mount_disk)
        logging.debug("%s", output[1])
        if mount_disk:
            mount_success = mount_and_dd(session, mount_disk)
            if not mount_success:
                raise exceptions.TestFail("Mount failed")
        else:
            raise exceptions.TestFail("Partition not available for disk")
        logging.debug("Unmounting disk")
        session.cmd_status_output('umount %s' % mount_disk)
        output = session.cmd_status_output('mount')
        logging.debug("%s", output[1])
        mount_success = mount_and_dd(session, mount_disk)
        if not mount_success:
            raise exceptions.TestFail("Mount failed")
        logging.debug("Unmounting disk")
        session.cmd_status_output('umount %s' % mount_disk)
        session.close()

        # detach disk from vm
        dev_detach_status = virsh.detach_device(vm_name, disk_xml,
                                                debug=True)
        utlv.check_exit_status(dev_detach_status)

    finally:
        vm.destroy(gracefully=False)
        vmxml_backup.sync()
        logging.debug('Destroying pool %s', pool_name)
        virsh.pool_destroy(pool_name)
        logging.debug('Undefining pool %s', pool_name)
        virsh.pool_undefine(pool_name)
        if os.path.exists(pool_xml_f):
            os.remove(pool_xml_f)
        if os.path.exists(disk_xml):
            data_dir.clean_tmp_files()
            logging.debug("Cleanup disk xml")
        if pre_def_pool == "yes":
            # Do not apply cleanup_pool for logical pool, logical pool will
            # be cleaned below
            pvt.cleanup_pool(pool_name, pool_type, pool_target,
                             emulated_image, **pool_kwargs)
        if (test_unit and
                (need_vol_create == "yes" and (pre_def_pool == "no")) and
                (pool_type == "logical")):
            process.system('lvremove -f %s/%s' % (pool_name, test_unit),
                           verbose=True)
            process.system('vgremove -f %s' % pool_name, verbose=True)
            process.system('pvremove -f %s' % source_dev, verbose=True)
        if new_vhbas:
            utils_npiv.vhbas_cleanup(new_vhbas)
        # Restart multipathd, this is to avoid bz1399075
        if source_dev:
            utils_misc.wait_for(lambda: utils_npiv.restart_multipathd(source_dev),
                                _DELAY_TIME*5, 0.0, 5.0)
        elif mpath_vol_path:
            utils_misc.wait_for(lambda: utils_npiv.restart_multipathd(mpath_vol_path),
                                _DELAY_TIME*5, 0.0, 5.0)
        else:
            utils_npiv.restart_multipathd()
        if old_mpath_conf:
            utils_npiv.prepare_multipath_conf(conf_path=mpath_conf_path,
                                              conf_content=old_mpath_conf,
                                              replace_existing=True)
        if not original_mpath_conf_exist and os.path.exists(mpath_conf_path):
            os.remove(mpath_conf_path)
