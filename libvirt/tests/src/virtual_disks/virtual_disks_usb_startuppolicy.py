import logging
import platform
import shutil
import time

import aexpect

from virttest import utils_disk
from virttest import utils_misc
from virttest import virt_vm, remote

from virttest.utils_libvirt import libvirt_usb
from virttest.utils_libvirt import libvirt_disk

from virttest.libvirt_xml import vm_xml, xcepts

LOG = logging.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test usb virtual disk startup policy.

    1.Prepare a vm with usb type disk with related startup policy
    2.Attach the virtual disk to the vm
    3.Start vm
    4.Check the disk in vm
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    def get_new_disks(old_partitions):
        """
        Get new virtual disks in VM after disk plug.

        :param old_partitions: already existing partitions in VM
        :return: New disks/partitions in VM
        """
        session = None
        try:
            session = vm.wait_for_login()
            if platform.platform().count('ppc64'):
                time.sleep(10)
            added_partitions = utils_disk.get_added_parts(session, old_partitions)
            LOG.debug("Newly added partition(s) is: %s", added_partitions)
            return added_partitions
        except Exception as err:
            test.fail("Error happens when get new disk: %s", str(err))
        finally:
            if session:
                session.close()

    def check_usb_disk_io(partition):
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

    def get_usable_usb_device():
        """
        Get usable usb devices

        """
        usable_usb = None
        all_usbs = libvirt_usb.get_usbs_lists_on_host()
        for usb in all_usbs:
            if ((usb.split())[5]) in usb_device_label:
                usable_usb = usb
                break
        if usable_usb is None:
            test.cancel("No usable USB device found.")

    def create_customized_disk(params):
        """
        Create one customized disk with related attributes

        :param params: dict wrapped with params
        """
        type_name = params.get("type_name")
        disk_device = params.get("device_type")
        device_target = params.get("target_dev")
        device_bus = params.get("target_bus")
        device_format = params.get("target_format")
        source_file_path = params.get("virt_disk_device_source")
        source_dict = {}
        if source_file_path:
            if 'block' in type_name:
                source_dict.update({"dev": source_file_path})
            else:
                source_dict.update({"file": source_file_path})
        startup_policy = params.get("startup_policy_value")
        if startup_policy:
            source_dict.update({"startupPolicy": startup_policy})
        disk_src_dict = {"attrs": source_dict}
        customized_disk = libvirt_disk.create_primitive_disk_xml(
            type_name, disk_device,
            device_target, device_bus,
            device_format, disk_src_dict, None)
        LOG.debug("create customized xml: %s", customized_disk)
        return customized_disk

    usb_device_label = params.get("usb_device_label")
    if 'ENTER.YOUR.DEV.NAME' in usb_device_label:
        test.cancel("please specify usable usb device labels in cfg file")

    hotplug = "yes" == params.get("virt_device_hotplug")
    status_error = "yes" == params.get("status_error")
    define_error = "yes" == params.get("define_error")
    pkgs_host = params.get("pkgs_host", "")

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
        get_usable_usb_device()
        # install essential package usbutils in host
        if not shutil.which('lsusb'):
            test.error("package {} not installed, please install them before testing".format(pkgs_host))

        device_obj = create_customized_disk(params)
        if not hotplug:
            vmxml.add_device(device_obj)
            vmxml.sync()
        vm.start()
        vm.wait_for_login().close()
    except virt_vm.VMStartError as details:
        if status_error:
            LOG.debug("VM failed to start as expected."
                      "Error: %s", str(details))
            usb_start_error_message = params.get('usb_start_error_message')
            if usb_start_error_message and not str(details).count(usb_start_error_message):
                test.fail('VM start error should contain messages:\n%s' % usb_start_error_message)
    except xcepts.LibvirtXMLError as xml_error:
        if not define_error:
            test.fail("Failed to define VM:\n%s" % xml_error)
        else:
            LOG.debug("VM failed to define as expected."
                      "Error: %s", str(xml_error))
    else:
        utils_misc.wait_for(lambda: get_new_disks(old_partitions), 20)
        new_disks = get_new_disks(old_partitions)
        if len(new_disks) != 1:
            test.fail("Attached 1 virtual disk but got %s." % len(new_disks))
        new_disk = new_disks[0]
        if platform.platform().count('ppc64'):
            time.sleep(10)
        session = vm.wait_for_login()
        if not libvirt_usb.check_usb_disk_type_in_vm(session, new_disk):
            test.fail("The newly attached disk is not a usb one.")
        session.close()
        if not check_usb_disk_io(new_disk):
            test.fail("Cannot operate the newly added disk in vm.")
    finally:
        if vm.is_alive():
            vm.destroy(gracefully=False)
        # Restoring vm
        vmxml_backup.sync()
