import logging
import aexpect

from avocado.utils import process

from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest import utils_npiv
from virttest import utils_disk
from virttest import utils_misc


_TIMEOUT = 5


def run(test, params, env):
    """
    Test for vhba hostdev passthrough.
    1. create a vhba
    2. prepare hostdev xml for lun device of the newly created vhba
    3.1 If hot attach, attach-device the hostdev xml to vm
    3.2 If cold attach, add the hostdev to vm and start it
    4. login the vm and check the attached disk
    5. detach-device the hostdev xml
    6. login the vm to check the partitions
    """

    def check_in_vm(vm, target, old_parts):
        """
        Check mount/read/write disk in VM.

        :param vm: VM guest.
        :param target: Disk dev in VM.
        :return: True if check successfully.
       """
        try:
            def get_attached_disk():
                session = vm.wait_for_login()
                new_parts = utils_disk.get_parts_list(session)
                session.close()
                added_parts = list(set(new_parts).difference(set(old_parts)))
                return added_parts
            added_parts = utils_misc.wait_for(get_attached_disk, _TIMEOUT)
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
                logging.error("Can't see added partition in VM")
                return False

            cmd = ("fdisk -l /dev/{0} && mkfs.ext4 -F /dev/{0} && "
                   "mkdir -p test && mount /dev/{0} test && echo"
                   " teststring > test/testfile && umount test"
                   .format(added_part))
            try:
                cmd_status, cmd_output = session.cmd_status_output(cmd)
            except Exception as detail:
                test.error("Error occurred when run cmd: fdisk, %s" % detail)
            logging.info("Check disk operation in VM:\n%s", cmd_output)
            session.close()
            if cmd_status != 0:
                return False
            return True
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as detail:
            logging.error(str(detail))
            return False

    try:
        status_error = "yes" == params.get("status_error", "no")
        vm_name = params.get("main_vm", "avocado-vt-vm1")
        device_target = params.get("hostdev_disk_target", "hdb")
        scsi_wwnn = params.get("scsi_wwnn", "ENTER.YOUR.WWNN")
        scsi_wwpn = params.get("scsi_wwpn", "ENTER.YOUR.WWPN")
        attach_method = params.get('attach_method', 'hot')
        vm = env.get_vm(vm_name)
        vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        virsh_dargs = {'debug': True, 'ignore_status': True}
        new_vhbas = []

        if scsi_wwnn.count("ENTER.YOUR.WWNN") or \
                scsi_wwpn.count("ENTER.YOUR.WWPN"):
            test.cancel("You didn't provide proper wwpn/wwnn")
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()
        old_parts = utils_disk.get_parts_list(session)
        # find first online hba
        online_hbas = []
        online_hbas = utils_npiv.find_hbas("hba")
        if not online_hbas:
            test.cancel("NO ONLINE HBAs!")
        first_online_hba = online_hbas[0]
        # create vhba based on the first online hba
        old_vhbas = utils_npiv.find_hbas("vhba")
        logging.debug("Original online vHBAs: %s", old_vhbas)
        new_vhba = utils_npiv.nodedev_create_from_xml(
                {"nodedev_parent": first_online_hba,
                 "scsi_wwnn": scsi_wwnn,
                 "scsi_wwpn": scsi_wwpn})
        # enable multipath service
        process.run("mpathconf --enable", shell=True)
        if not utils_misc.wait_for(lambda: utils_npiv.is_vhbas_added(old_vhbas),
                                   timeout=_TIMEOUT):
            test.fail("vhba not successfully created")
        new_vhbas.append(new_vhba)
        # find first available lun of the newly created vhba
        lun_dicts = []
        first_lun = {}
        if not utils_misc.wait_for(lambda: utils_npiv.find_scsi_luns(new_vhba),
                                   timeout=_TIMEOUT):
            test.fail("There is no available lun storage for "
                      "wwpn: %s, please check your wwns or "
                      "contact IT admins" % scsi_wwpn)
        lun_dicts = utils_npiv.find_scsi_luns(new_vhba)
        logging.debug("The luns discovered are: %s", lun_dicts)
        first_lun = lun_dicts[0]
        # prepare hostdev xml for the first lun
        kwargs = {'addr_bus': first_lun['bus'],
                  'addr_target': first_lun['target'],
                  'addr_unit': first_lun['unit']}

        new_hostdev_xml = utils_npiv.create_hostdev_xml(
                   adapter_name="scsi_host"+first_lun['scsi'],
                   **kwargs)
        logging.info("New hostdev xml as follow:")
        logging.info(new_hostdev_xml)
        new_hostdev_xml.xmltreefile.write()
        if attach_method == "hot":
            # attach-device the lun's hostdev xml to guest vm
            result = virsh.attach_device(vm_name, new_hostdev_xml.xml)
            libvirt.check_exit_status(result, status_error)
        elif attach_method == "cold":
            if vm.is_alive():
                vm.destroy(gracefully=False)
            vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            vmxml.devices = vmxml.devices.append(new_hostdev_xml)
            vmxml.sync()
            vm.start()
            session = vm.wait_for_login()
            logging.debug("The new vm's xml is: \n%s", vmxml)

        # login vm and check the disk
        check_result = check_in_vm(vm, device_target, old_parts)
        if not check_result:
            test.fail("check disk in vm failed")
        result = virsh.detach_device(vm_name, new_hostdev_xml.xml)
        libvirt.check_exit_status(result, status_error)
        # login vm and check disk actually removed
        if not vm.session:
            session = vm.wait_for_login()
        parts_after_detach = utils_disk.get_parts_list(session)
        old_parts.sort()
        parts_after_detach.sort()
        if parts_after_detach == old_parts:
            logging.info("hostdev successfully detached.")
        else:
            test.fail("Device not successfully detached. "
                      "Still existing in vm's /proc/partitions")
    finally:
        utils_npiv.vhbas_cleanup(new_vhbas)
        # recover vm
        if vm.is_alive():
            vm.destroy(gracefully=False)
        logging.info("Restoring vm...")
        vmxml_backup.sync()
        process.system('service multipathd restart', verbose=True)
