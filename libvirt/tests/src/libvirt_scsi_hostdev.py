import logging
import re
import aexpect
import platform
#import time
import os

from avocado.utils import process

from virttest import virt_vm
from virttest import remote
from virttest import virsh
from virttest import utils_disk
from virttest import utils_misc
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest import libvirt_version

from virttest.libvirt_xml.devices import hostdev
from virttest.libvirt_xml.devices.controller import Controller


def run(test, params, env):
    """
    Test hosted scsi device passthrough
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}

    def prepare_hostdev_xml(**kwargs):
        """
        Prepare the scsi device's xml

        :param kwargs: The arguments to generate scsi host device xml.
        :return: The xml of the scsi host device.
        """
        hostdev_xml = hostdev.Hostdev()
        hostdev_xml.type = "scsi"
        if kwargs.get("managed"):
            hostdev_xml.managed = kwargs.get("managed")
        hostdev_xml.mode = kwargs.get("mode", "subsystem")
        if kwargs.get("sgio"):
            hostdev_xml.sgio = kwargs.get("sgio")
        if kwargs.get("rawio"):
            hostdev_xml.rawio = kwargs.get("rawio")
        hostdev_xml.readonly = "yes" == kwargs.get("readonly")
        hostdev_xml.shareable = "yes" == kwargs.get("shareable")

        source_args = {}
        source_protocol = kwargs.get("source_protocol")
        if source_protocol == "iscsi":
            # Use iscsi lun directly
            source_args['protocol'] = "iscsi"
            source_args['host_name'] = kwargs.get("iscsi_host", "ISCSI_HOST")
            source_args['host_port'] = kwargs.get("iscsi_port", "ISCSI_PORT")
            source_args['source_name'] = kwargs.get("iqn_name", "IQN_NAME")
            source_args['auth_user'] = kwargs.get("auth_user")
            source_args['secret_type'] = kwargs.get("secret_type")
            source_args['secret_uuid'] = kwargs.get("secret_uuid")
            source_args['secret_usage'] = kwargs.get("secret_usage")
            source_args['iqn_id'] = kwargs.get("iqn_id")
        elif source_protocol:
            test.cancel("We do not support source protocol = %s yet" %
                        source_protocol)
        else:
            # Use local scsi device
            source_args['adapter_name'] = kwargs.get("adapter_name",
                                                     "scsi_host999")
            source_args['bus'] = kwargs.get("addr_bus", "0")
            source_args['target'] = kwargs.get('addr_target', "0")
            source_args['unit'] = kwargs.get('addr_unit', "0")
        # If any attributes not used, remove them from source dict to avoid
        # attr="" or attr="None" situation.
        for key, value in list(source_args.items()):
            if not value:
                source_args.pop(key)
        hostdev_xml.source = hostdev_xml.new_source(**source_args)
        logging.info("hostdev xml is: %s", hostdev_xml)
        return hostdev_xml

    def prepare_iscsi_lun(emulated_img="emulated-iscsi", img_size='1G'):
        """
        Prepare iscsi lun

        :param emulated_img: The name of the iscsi lun device.
        :param img_size: The size of the iscsi lun device.
        :return: The iscsi target and lun number.
        """
        enable_chap_auth = "yes" == params.get("enable_chap_auth")
        if enable_chap_auth:
            chap_user = params.get("chap_user", "redhat")
            chap_passwd = params.get("chap_passwd", "password")
        else:
            chap_user = ""
            chap_passwd = ""
        iscsi_target, lun_num = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                               is_login=False,
                                                               emulated_image=emulated_img,
                                                               image_size=img_size,
                                                               chap_user=chap_user,
                                                               chap_passwd=chap_passwd,
                                                               portal_ip="127.0.0.1")
        return iscsi_target, lun_num

    def prepare_local_scsi(emulated_img="emulated-iscsi", img_size='1G'):
        """
        Prepare a local scsi device

        :param emulated_img: The name of the iscsi lun device.
        :param img_size: The size of the iscsi lun device.
        :return: The iscsi scsi/bus/target/unit number.
        """
        lun_info = []
        device_source = libvirt.setup_or_cleanup_iscsi(is_setup=True,
                                                       is_login=True,
                                                       emulated_image=emulated_img,
                                                       image_size=img_size)
        cmd = "targetcli ls"
        cmd_result = process.run(cmd, shell=True)
        logging.debug("new block device is: %s", device_source)
        cmd = "lsscsi | grep %s | awk '{print $1}'" % device_source
        cmd_result = process.run(cmd, shell=True)
        lun_info = re.findall("\d+", str(cmd_result.stdout.strip()))
        if len(lun_info) != 4:
            test.fail("Get wrong scsi lun info: %s" % lun_info)
        scsi_num = lun_info[0]
        bus_num = lun_info[1]
        target_num = lun_info[2]
        unit_num = lun_info[3]
        return scsi_num, bus_num, target_num, unit_num

    def get_new_disks(vm, old_partitions):
        """
        Get new disks in vm after hostdev plug.

        :param vm: The vm to be checked.
        :param old_partitions: Already existing partitions in vm.
        :return: New disks/partitions in vm, or None if no new disk/partitions.
        """
        try:
            session = vm.wait_for_login()
            if platform.platform().count('ppc64'):
                logging.debug("PPC machine may need a little sleep time "
                              "to see all disks, related owner may need "
                              "further investigation. Skip the sleep for now.")
                #time.sleep(10)
            new_partitions = utils_disk.get_parts_list(session)
            logging.debug("new partitions are: %s", new_partitions)
            added_partitions = list(set(new_partitions).difference(set(old_partitions)))
            session.close()
            if not added_partitions:
                logging.debug("No new partitions found in vm.")
            else:
                logging.debug("Newly added partition(s) is: %s", added_partitions)
            return added_partitions
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as err:
            test.fail("Error happens when get new disk: %s" % str(err))

    def get_unpriv_sgio(scsi_dev):
        """
        Get scsi dev's unpriv_sgio value.

        :param scsi_dev: The scsi device to be checked.
        :return: The unpriv_sgio value of the scsi device.
        """
        cmd = "lsscsi -g | grep '\[%s\]'" % scsi_dev
        try:
            output = process.system_output(cmd, verbose=True, shell=True)
            blkdev = output.split()[-2]
            chardev = output.split()[-1]
            blk_stat = os.stat(blkdev)
            sg_stat = os.stat(chardev)
            blkdev_major = os.major(blk_stat.st_rdev)
            blkdev_minor = os.minor(blk_stat.st_rdev)
            chardev_major = os.major(sg_stat.st_rdev)
            chardev_minor = os.minor(sg_stat.st_rdev)
            blkdev_unpriv_path = ("/sys/dev/block/%s:%s/queue/unpriv_sgio" %
                                  (blkdev_major, blkdev_minor))
            chardev_unpriv_path = ("/sys/dev/char/%s:%s/device/unpriv_sgio" %
                                   (chardev_major, chardev_minor))
            # unpriv_sgio feature change in centain kernel,e.g: /sys/dev/char/%s:%s/queue/unpriv_sgio may not exist
            if os.path.exists(blkdev_unpriv_path) is False:
                return
            with open(blkdev_unpriv_path, 'r') as f:
                blkdev_unpriv_value = f.read().strip()
            with open(chardev_unpriv_path, 'r') as f:
                chardev_unpriv_value = f.read().strip()
            logging.debug("blkdev unpriv_sgio:%s\nchardev unpriv_sgio:%s",
                          blkdev_unpriv_value, chardev_unpriv_value)
            if ((not blkdev_unpriv_value or not chardev_unpriv_value) or
                    (blkdev_unpriv_value != chardev_unpriv_value)):
                test.error("unpriv_sgio values are incorrect under block "
                           "and char folders.")
            return blkdev_unpriv_value
        except Exception as detail:
            test.fail("Error happens when try to get the unpriv_sgio value: %s"
                      % detail)

    def check_unpriv_sgio(scsi_dev, unpriv_sgio=False, shareable_dev=True):
        """
        Check device's unpriv_sgio value with provided boolean value.

        :param scsi_dev: The scsi device to be checked.
        :param unpriv_sgio: If the expected unpriv_sgio is True or False.
        :param shareable_dev: If the device is a shareable one.
        """
        scsi_unpriv_sgio = get_unpriv_sgio(scsi_dev)
        # On rhel9, previously skip check folder in get_unpriv_sgio(),so here return True directly
        if scsi_unpriv_sgio is None:
            return True
        if shareable_dev:
            # Only when <shareable/> set, the sgio takes effect.
            if ((unpriv_sgio and scsi_unpriv_sgio == '1') or
                    (not unpriv_sgio and scsi_unpriv_sgio == '0')):
                return True
        else:
            if scsi_unpriv_sgio == '0':
                return True
        return False

    def check_disk_io(vm, partition):
        """
        Check if the disk partition in vm can be normally used.

        :param vm: The vm to be checked.
        :param partition: The disk partition in vm to be checked.
        :return: If the disk can be used, return True.
        """
        readonly = "yes" == params.get("readonly")
        readonly_keywords = ['readonly', 'read-only', 'read only']
        try:
            session = vm.wait_for_login()
            cmd = ("fdisk -l /dev/{0} && mkfs.ext4 -F /dev/{0} && "
                   "mkdir -p {0} && mount /dev/{0} {0} && echo"
                   " teststring > {0}/testfile && umount {0}"
                   .format(partition))
            status, output = session.cmd_status_output(cmd)
            session.close()
            logging.debug("Disk operation in VM:\nexit code:\n%s\noutput:\n%s",
                          status, output)
            if readonly:
                for ro_kw in readonly_keywords:
                    if ro_kw in str(output).lower():
                        return True
                logging.error("Hostdev set with 'readonly'. "
                              "But still can be operated.")
                return False
            return status == 0
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as err:
            logging.debug("Error happens when check disk io in vm: %s",
                          str(err))
            return False

    def ppc_controller_update(vmxml):
        """
        Update controller of ppc vm to 'virtio-scsi' to support 'scsi' type
        :return:
        """
        device_bus = 'scsi'
        if params.get('machine_type') == 'pseries':
            if not vmxml.get_controllers(device_bus, 'virtio-scsi'):
                vmxml.del_controller(device_bus)
                ppc_controller = Controller('controller')
                ppc_controller.type = device_bus
                ppc_controller.index = '0'
                ppc_controller.model = 'virtio-scsi'
                vmxml.add_device(ppc_controller)
                vmxml.sync()

    coldplug = "cold_plug" == params.get("attach_method")
    hotplug = "hot_plug" == params.get("attach_method")
    status_error = "yes" == params.get("status_error")
    use_iscsi_directly = "iscsi" == params.get("source_protocol")
    sgio = params.get("sgio")
    test_shareable = "yes" == params.get("shareable")
    device_num = int(params.get("device_num", "1"))
    new_disks = []
    new_disk = ""
    attach_options = ""
    iscsi_target = ""
    lun_num = ""
    adapter_name = ""
    addr_scsi = ""
    addr_bus = ""
    addr_target = ""
    addr_unit = ""
    auth_sec_uuid = ""
    hostdev_xmls = []

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    virsh_dargs = {'debug': True, 'ignore_status': True}
    enable_initiator_set = "yes" == params.get("enable_initiator_set", "no")
    if enable_initiator_set and not libvirt_version.version_compare(6, 7, 0):
        test.cancel("current version doesn't support iscsi initiator hostdev feature")
    try:
        # Load sg module if necessary
        process.run("modprobe sg", shell=True, ignore_status=True, verbose=True)
        # Backup vms' xml
        vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml = vmxml_backup.copy()
        ppc_controller_update(vmxml)
        if test_shareable:
            vm_names = params.get("vms").split()
            if len(vm_names) < 2:
                test.error("At least 2 vms should be prepared "
                           "for shareable test.")
            vm2_name = vm_names[1]
            vm2 = env.get_vm(vm2_name)
            vm2_xml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm2_name)
            vm2_xml = vm2_xml_backup.copy()
            ppc_controller_update(vm2_xml)
            if vm2.is_dead():
                vm2.start()
                session = vm2.wait_for_login()
                vm2_old_partitions = utils_disk.get_parts_list(session)
                session.close()

        # Get disk partitions info before hot/cold plug virtual disk
        if vm.is_dead():
            vm.start()
        session = vm.wait_for_login()
        old_partitions = utils_disk.get_parts_list(session)
        session.close()
        for dev_num in range(device_num):
            if use_iscsi_directly:
                iscsi_target, lun_num = prepare_iscsi_lun(emulated_img='img'+str(dev_num))
                params['iscsi_host'] = "127.0.0.1"
                params['iscsi_port'] = "3260"
                params['iqn_name'] = iscsi_target + "/" + lun_num
            else:
                addr_scsi, addr_bus, addr_target, addr_unit = prepare_local_scsi(emulated_img='img'+str(dev_num))
                if not params.get('adapter_name') or dev_num >= 1:
                    params['adapter_name'] = "scsi_host" + addr_scsi
                params['addr_bus'] = addr_bus
                params['addr_target'] = addr_target
                params['addr_unit'] = addr_unit
                lsscsi_keyword = (addr_scsi + ":" + addr_bus + ":" + addr_target
                                  + ":" + addr_unit)

            enable_chap_auth = "yes" == params.get("enable_chap_auth")
            auth_sec_usage = params.get("auth_sec_usage", "libvirtiscsi")
            if enable_chap_auth:
                chap_user = params.get("chap_user", "redhat")
                chap_passwd = params.get("chap_password", "password")
                auth_sec_dict = {"sec_usage": "iscsi", "sec_target": auth_sec_usage}
                auth_sec_uuid = libvirt.create_secret(auth_sec_dict)
                virsh.secret_set_value(auth_sec_uuid, chap_passwd,
                                       encode=True, debug=True)
                params['auth_user'] = chap_user
                params['secret_type'] = "iscsi"
                params['secret_uuid'] = auth_sec_uuid

            if enable_initiator_set:
                params['iqn_id'] = iscsi_target
            hostdev_xml = prepare_hostdev_xml(**params)
            hostdev_xmls.append(hostdev_xml)

        if coldplug:
            attach_options = "--config"
        # Attach virtual disk to vm
        for dev_num in range(device_num):
            result = virsh.attach_device(vm_name, hostdev_xmls[dev_num].xml,
                                         flagstr=attach_options,
                                         ignore_status=True, debug=True)
            libvirt.check_exit_status(result, status_error & hotplug)
        if coldplug:
            vm.destroy(gracefully=False)
            result = virsh.start(vm_name, ignore_status=True, debug=True)
            libvirt.check_exit_status(result, status_error & coldplug)
        if not status_error:
            vm.wait_for_login().close()
            # Here we may need to wait for sometime, update if issue happens
            # again.
            #time.sleep(10)
            utils_misc.wait_for(lambda: get_new_disks(vm, old_partitions), 20)
            new_disks = get_new_disks(vm, old_partitions)
            if len(new_disks) != device_num:
                test.fail("Attached %s virtual disk but got %s." %
                          (device_num, len(new_disks)))
            new_disk = new_disks[0]
            for new_disk in new_disks:
                # Check disk io of the hostdev in vm.
                if not check_disk_io(vm, new_disk):
                    test.fail("Got unexpected result when operate the newly "
                              "added disk in vm.")

                # Check if unpri_sgio value correctly set by the xml sgio param.
                if not use_iscsi_directly:
                    if sgio == "unfiltered":
                        unpriv_sgio = True
                    else:
                        unpriv_sgio = False
                    if not(check_unpriv_sgio(lsscsi_keyword, unpriv_sgio, test_shareable)):
                        test.fail("SCSI dev's unpriv_sgio value is inconsistent with "
                                  "hostdev xml's sgio value.")

                # Check shareable device.
                if test_shareable:
                    vm2_xml.add_device(hostdev_xml)
                    session = vm2.wait_for_login()
                    result = virsh.attach_device(vm2_name, hostdev_xml.xml,
                                                 ignore_status=False, debug=True)
                    utils_misc.wait_for(lambda: get_new_disks(vm2, vm2_old_partitions), 20)
                    vm2_new_disks = get_new_disks(vm2, vm2_old_partitions)
                    if len(vm2_new_disks) != 1:
                        test.fail("In second vm, attached 1 virtual disk but got %s." %
                                  len(vm2_new_disks))
                    vm2_new_disk = vm2_new_disks[0]
                    if not check_disk_io(vm2, vm2_new_disk):
                        test.fail("Testing shareable device, got unexpected result "
                                  "when operate the newly added disk in the second vm.")

            # Detach the disk from vm.
            for dev_num in range(device_num):
                result = virsh.detach_device(vm_name, hostdev_xmls[dev_num].xml,
                                             flagstr=attach_options,
                                             ignore_status=False, debug=True)

            # Check the detached disk in vm.
            if coldplug:
                vm.destroy(gracefully=False)
                vm.start()
                vm.wait_for_login().close()
            utils_misc.wait_for(lambda: not get_new_disks(vm, old_partitions), 20)
            new_disks = get_new_disks(vm, old_partitions)
            if len(new_disks) != 0:
                test.fail("Unplug virtual disk failed.")
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Restoring vm
        vmxml_backup.sync()
        if test_shareable:
            if vm2.is_alive():
                vm2.destroy(gracefully=False)
            vm2_xml_backup.sync()
        if auth_sec_uuid:
            virsh.secret_undefine(auth_sec_uuid)
        for dev_num in range(device_num):
            libvirt.setup_or_cleanup_iscsi(is_setup=False, emulated_image='img'+str(dev_num))
