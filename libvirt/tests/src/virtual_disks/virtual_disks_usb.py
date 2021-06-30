import os
import time
import logging
import platform
import aexpect

from virttest import virsh
from virttest import utils_disk
from virttest import utils_misc
from virttest import virt_vm, remote
from virttest import libvirt_version
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.controller import Controller
from virttest import data_dir


def run(test, params, env):
    """
    Test usb virtual disk plug/unplug.

    1.Prepare a vm with usb controllers
    2.Prepare a local image
    3.Prepare a virtual disk xml
    4.Attach the virtual disk to the vm
    5.Check the disk in vm
    6.Unplug the disk from vm
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def get_new_disks(old_partitions):
        """
        Get new virtual disks in vm after disk plug.

        :param old_partitions: Already existing partitions in vm.
        :return: New disks/partitions in vm.
        """
        session = None
        try:
            session = vm.wait_for_login()
            if platform.platform().count('ppc64'):
                time.sleep(10)
            new_partitions = utils_disk.get_parts_list(session)
            added_partitions = list(set(new_partitions).difference(set(old_partitions)))
            if not added_partitions:
                logging.debug("No new partitions found in vm.")
            else:
                logging.debug("Newly added partition(s) is: %s", added_partitions)
            return added_partitions
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as err:
            test.fail("Error happens when get new disk: %s", str(err))
        finally:
            if session:
                session.close()

    def check_disk_type(partition):
        """
        Check if a disk partition is a usb disk in vm.

        :param partition: The disk partition in vm to be checked.
        :return: If the disk is a usb device, return True.
        """
        session = None
        try:
            session = vm.wait_for_login()
            if platform.platform().count('ppc64'):
                time.sleep(10)
            cmd = "ls -l /dev/disk/by-id/ | grep %s | grep -i usb" % partition
            status = session.cmd_status(cmd)
            return status == 0
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as err:
            logging.debug("Error happens when check if new disk is usb device: %s",
                          str(err))
            return False
        finally:
            if session:
                session.close()

    def check_disk_io(partition):
        """
        Check if the disk partition in vm can be normally used.

        :param partition: The disk partition in vm to be checked.
        :return: If the disk can be used, return True.
        """
        session = None
        try:
            session = vm.wait_for_login()
            cmd = ("fdisk -l /dev/{0} && mkfs.ext4 -F /dev/{0} && "
                   "mkdir -p test && mount /dev/{0} test && echo"
                   " teststring > test/testfile && umount test"
                   .format(partition))
            status, output = session.cmd_status_output(cmd)
            logging.debug("Disk operation in VM:\nexit code:\n%s\noutput:\n%s",
                          status, output)
            return status == 0
        except (remote.LoginError, virt_vm.VMError, aexpect.ShellError) as err:
            logging.debug("Error happens when check disk io in vm: %s", str(err))
            return False
        finally:
            if session:
                session.close()

    def remove_usbs(vmxml):
        """
        Remove all USB devices and controllers from a vm's xml.

        :param vmxml: The vm's xml.
        """
        try:
            for xml in vmxml.xmltreefile.findall('/devices/*'):
                if (xml.get('bus') == 'usb') or (xml.get('type') == 'usb'):
                    vmxml.xmltreefile.remove(xml)
        except (AttributeError, TypeError):
            pass  # Element doesn't exist at first
        vmxml.xmltreefile.write()

    def prepare_usb_controller(vmxml, usb_models):
        """
        Add usb controllers into vm's xml.

        :param vmxml: The vm's xml.
        :param usb_models: The usb models will be used in usb controller(s).
        """
        if not usb_models:
            test.error("No usb model provided.")
        # Add disk usb controller(s)
        for usb_model in usb_models:
            usb_controller = Controller("controller")
            usb_controller.type = "usb"
            usb_controller.index = "0"
            usb_controller.model = usb_model
            vmxml.add_device(usb_controller)
        # Redefine domain
        vmxml.sync()

    def prepare_local_image():
        """
        Prepare a local image.

        :return: The path to the image file.
        """
        image_filename = params.get("image_filename", "raw.img")
        image_format = params.get("image_format", "raw")
        image_size = params.get("image_size", "1G")
        image_path = os.path.join(data_dir.get_data_dir(), image_filename)
        try:
            if image_format in ["raw", "qcow2"]:
                image_path = libvirt.create_local_disk("file",
                                                       image_path, image_size,
                                                       disk_format=image_format)
            else:
                test.cancel("We only test raw & qcow2 format for now.")
        except Exception as err:
            test.error("Error happens when prepare local image: %s", err)
        return image_path

    def prepare_virt_disk_xml(image_path):
        """
        Prepare the virtual disk xml to be attached/detached.

        :param image_path: The path to the local image.
        :return: The virtual disk xml.
        """
        virt_disk_device = params.get("virt_disk_device", "disk")
        virt_disk_device_type = params.get("virt_disk_device_type", "file")
        virt_disk_device_format = params.get("virt_disk_device_format", "raw")
        virt_disk_device_target = params.get("virt_disk_device_target", "sdb")
        virt_disk_device_bus = params.get("virt_disk_device_bus", "usb")
        disk_xml = Disk(type_name=virt_disk_device_type)
        disk_xml.device = virt_disk_device
        disk_src_dict = {'attrs': {'file': image_path, 'type_name': 'file'}}
        disk_xml.source = disk_xml.new_disk_source(**disk_src_dict)
        driver_dict = {"name": "qemu", "type": virt_disk_device_format}
        disk_xml.driver = driver_dict
        disk_xml.target = {"dev": virt_disk_device_target,
                           "bus": virt_disk_device_bus}
        return disk_xml

    usb_models = params.get("usb_model").split()
    coldplug = "yes" == params.get("coldplug")
    status_error = "yes" == params.get("status_error")
    new_disks = []
    new_disk = ""
    attach_options = ""

    # Get disk partitions info before hot/cold plug virtual disk
    if vm.is_dead():
        vm.start()
    session = vm.wait_for_login()
    old_partitions = utils_disk.get_parts_list(session)
    session.close()
    vm.destroy(gracefully=False)

    # Backup vm xml
    vmxml_backup = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    vmxml = vmxml_backup.copy()

    try:
        if 'vt82c686b-uhci' in usb_models and libvirt_version.version_compare(7, 4, 0):
            test.cancel("vt82c686b-usb-uhci not supported in this QEMU")
        remove_usbs(vmxml)
        prepare_usb_controller(vmxml, usb_models)
        vm.start()
        session = vm.wait_for_login()
        disk_xml = prepare_virt_disk_xml(prepare_local_image())
        session.close()
        if coldplug:
            attach_options = "--config"

        # Attach virtual disk to vm
        result = virsh.attach_device(vm_name, disk_xml.xml,
                                     flagstr=attach_options,
                                     ignore_status=True, debug=True)
        libvirt.check_exit_status(result, status_error)

        # Check the attached disk in vm
        if coldplug:
            vm.destroy(gracefully=False)
            vm.start()
            vm.wait_for_login().close()
        utils_misc.wait_for(lambda: get_new_disks(old_partitions), 20)
        new_disks = get_new_disks(old_partitions)
        if len(new_disks) != 1:
            test.fail("Attached 1 virtual disk but got %s." % len(new_disk))
        new_disk = new_disks[0]
        if not check_disk_type(new_disk):
            test.fail("The newly attached disk is not a usb one.")
        if not check_disk_io(new_disk):
            test.fail("Cannot operate the newly added disk in vm.")

        # Detach the disk from vm
        result = virsh.detach_device(vm_name, disk_xml.xml,
                                     flagstr=attach_options,
                                     ignore_status=True, debug=True, wait_remove_event=True)
        libvirt.check_exit_status(result, status_error)

        # Check the detached disk in vm
        if coldplug:
            vm.destroy(gracefully=False)
            vm.start()
            vm.wait_for_login().close()
        utils_misc.wait_for(lambda: not get_new_disks(old_partitions), 20)
        new_disks = get_new_disks(old_partitions)
        if len(new_disks) != 0:
            test.fail("Unplug virtual disk failed.")

    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Restoring vm
        vmxml_backup.sync()
