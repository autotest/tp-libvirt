import logging
from avocado.core import exceptions
from avocado.utils import process
from virttest import virt_vm
from virttest import virsh
from virttest.libvirt_xml.devices import hostdev
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from npiv import npiv_nodedev_create_destroy as nodedev
from virttest import utils_misc


_FC_HOST_PATH = "/sys/class/fc_host"


def create_hostdev_xml(adapter_name="", **kwargs):
    """
    Create vhba hostdev xml.

    :param adapter_name: The name of the scsi adapter
    :param **kwargs: Could contain addr_bus, addr_target, addr_unit, mode, and managed
    :return: an xml object set by **kwargs
    """
    addr_bus = kwargs.get('addr_bus', 0)
    addr_target = kwargs.get('addr_target', 0)
    addr_unit = kwargs.get('addr_unit', 0)
    mode = kwargs.get('mode', 'subsystem')
    managed = kwargs.get('managed', 'no')

    hostdev_xml = hostdev.Hostdev()
    hostdev_xml.hostdev_type = "scsi"
    hostdev_xml.managed = managed
    hostdev_xml.mode = mode

    source_args = {}
    source_args['adapter_name'] = adapter_name
    source_args['bus'] = addr_bus
    source_args['target'] = addr_target
    source_args['unit'] = addr_unit
    hostdev_xml.source = hostdev_xml.new_source(**source_args)
    logging.info(hostdev_xml)
    return hostdev_xml


def find_scsi_luns(scsi_host):
    """
    Find available luns of specified scsi_host.

    :param scsi_host: The scsi host name in foramt of "scsi_host#"
    :return: A dictionary contains all available fc luns
    """
    lun_dicts = []
    tmp_list = []
    scsi_number = scsi_host.replace("scsi_host", "")
    cmd = "multipath -ll | grep '\- %s:' | grep 'ready running' | awk -F ' ' '{print $3}'" % scsi_number
    result = process.run(cmd, shell=True)
    tmp_list = result.stdout.strip().splitlines()
    for lun in tmp_list:
        lun = lun.split(":")
        lun_dicts_item = {}
        lun_dicts_item["scsi"] = lun[0]
        lun_dicts_item["bus"] = lun[1]
        lun_dicts_item["target"] = lun[2]
        lun_dicts_item["unit"] = lun[3]
        lun_dicts.append(lun_dicts_item)
    return lun_dicts


def check_in_vm(vm, target, old_parts):
    """
    Check mount/read/write disk in VM.

    :param vm: VM guest.
    :param target: Disk dev in VM.
    :return: True if check successfully.
   """
    try:
        session = vm.wait_for_login()
        new_parts = libvirt.get_parts_list(session)
        added_parts = list(set(new_parts).difference(set(old_parts)))
        logging.info("Added parts:%s", added_parts)
        if len(added_parts) != 1:
            logging.error("The number of new partitions is invalid in VM")
            return False

        added_part = None
        if target.startswith("vd"):
            if added_parts[0].startswith("vd"):
                added_part = added_parts[0]
        elif target.startswith("hd"):
            if added_parts[0].startswith("sd"):
                added_part = added_parts[0]

        if not added_part:
            logging.error("Cann't see added partition in VM")
            return False

        cmd = ("fdisk -l /dev/{0} && mkfs.ext4 -F /dev/{0} && "
               "mkdir -p test && mount /dev/{0} test && echo"
               " teststring > test/testfile && umount test"
               .format(added_part))
        s, o = session.cmd_status_output(cmd)
        logging.info("Check disk operation in VM:\n%s", o)
        session.close()
        if s != 0:
            return False
        return True
    except (remote.LoginError, virt_vm.VMError, aexpect.ShellError), e:
        logging.error(str(e))
        return False


def run(test, params, env):
    """
    Test for vhba hostdev passthrough.

    1. create a vhba
    2. prepare hostdev xml for lun device of the newly created vhba
    3. attach-device the hostdev xml to vm
    4. login the vm and check the attached disk
    5. detach-device the hostdev xml
    6. login the vm to check the partitions
    """
    try:
        status_error = params.get("status_error", "no")
        vm_name = params.get("main_vm")
        device_target = params.get("hostdev_disk_target", "hdb")
        scsi_wwnn = params.get("scsi_wwnn", "")
        scsi_wwpn = params.get("scsi_wwpn", "")
        vm = env.get_vm(vm_name)
        vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        virsh_dargs = {'debug': True, 'ignore_status': True}
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()
        old_parts = libvirt.get_parts_list(session)
        #find first online hba
        online_hbas = []
        vhba_lists = []
        online_hbas = nodedev.find_hbas("hba")
        if not online_hbas:
            logging.error("NO ONLINE HBAS!")
        first_online_hba = online_hbas[0]
        #create vhba based on the first online hba
        old_vhbas = nodedev.find_hbas("vhbas")
        new_vhba = nodedev.nodedev_create_from_xml({"nodedev_parent": first_online_hba,
                                                    "scsi_wwnn": scsi_wwnn,
                                                    "scsi_wwpn": scsi_wwpn})
        if not utils_misc.wait_for(lambda: nodedev.is_vhbas_added(old_vhbas), timeout=5):
            logging.error("vhba not successfully created")
        #find first available lun of the newly created vhba
        lun_dicts = []
        first_lun = {}
        lun_dicts = find_scsi_luns(new_vhba)
        if not lun_dicts:
            raise exceptions.TestFail("There is no available lun storage for"
                                      "wwnn: %s, please check your wwns or"
                                      "contact IT admins", scsi_wwpn)
        first_lun = lun_dicts[0]
        #prepare hostdev xml for the first lun
        kwargs = {'addr_bus': first_lun['bus'],
                  'addr_target': first_lun['target'],
                  'addr_unit': first_lun['unit']}

        new_hostdev_xml = create_hostdev_xml(adapter_name="scsi_host"+first_lun['scsi'], **kwargs)
        logging.info("New hostdev xml as follow:")
        logging.info(new_hostdev_xml)
        new_hostdev_xml.xmltreefile.write()

        #attach-device the lun's hostdev xml to guest vm
        result = virsh.attach_device(vm_name, new_hostdev_xml.xml)
        status = result.exit_status
        if status_error == "yes":
            if status:
                raise exceptions.TestFail("Attach device failed: %s", result.stderr)
        #login vm and check the disk
        check_result = check_in_vm(vm, device_target, old_parts)
        if not check_result:
            raise exceptions.TestFail("check disk in vm failed")
        #unplug the hostdev, detach-device not working now https://bugzilla.redhat.com/show_bug.cgi?id=1318181
        result = virsh.detach_device(vm_name, new_hostdev_xml.xml)
        status = result.exit_status
        if status_error == "yes":
            if status:
                raise exceptions.TestFail("Detach device failed: %s", result.stderr)
        #login vm and check disk actually removed
        parts_after_detach = libvirt.get_parts_list(session)
        old_parts.sort()
        parts_after_detach.sort()
        if parts_after_detach == old_parts:
            logging.info("hostdev successfully detached.")
        else:
            raise exceptions.TestFail("Device not successfully detached. Still existing in vm's /proc/partitions")
    finally:
        nodedev.vhbas_cleanup()
        #recover vm
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()
