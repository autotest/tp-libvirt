import os
import logging
from avocado.core import data_dir
from avocado.core.exceptions import TestError
from provider.vfio import ccw
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_misc import cmd_status_output
from virttest import storage

def disk_for_import(vmxml):
    """
    Returns the absolute path to a disk image for import.
    Assume the boot image is the first disk and an image file.

    :param vmxml: VMXML instance
    """
    disks = vmxml.get_disk_all()
    disk_list = list(disks.values())
    first_disk = disk_list[0]
    return first_disk.find('source').get('file')


def mdev_nodedev_for(mdev_type):
    """ creates and returns name of a nodedev of type mdev_type """
    return "mdev_59ce75a4_7419_4426_8689_8d0c2002f23c_0_0_26aa"


def virt_install_with_hostdev(vm_name, mdev_nodedev, target_address, disk_path):
    """ Runs virt-install with hostdev"""
    cmd = ("virt-install --import --name %s"
           " --hostdev %s,%s"
           " --disk %s"
           " --vcpus 2 --memory 2048"
           " --nographics --noautoconsole" %
           (vm_name, mdev_nodedev, target_address, disk_path))
    err, out = cmd_status_output(cmd, shell=True, verbose=True)
    if err:
        raise TestError("Couldn't install vm with hostdev: %s" % out)


def target_address_for(address_type):
    """ returns a valid target device address """
    return "address.type=ccw,address.cssid=0xfe,address.ssid=0x0,address.devno=0x1111"


def run(test, params, env):
    """
    Confirm that a mediated device can be used by virt-install.
    For this we import a disk we know will boot
    and check the result inside the guest.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    mdev_type = params.get("mdev_type", "vfio_ccw-io")
    address_type = params.get("address_type", "ccw")

    try:

        vm.undefine()
        disk = disk_for_import(vmxml)
        mdev_nodedev = mdev_nodedev_for(mdev_type)
        target_address = target_address_for(address_type)
        virt_install_with_hostdev(vm_name, mdev_nodedev, target_address, disk)

        session = vm.wait_for_login()
        _, out = cmd_status_output("lscss", session=session,
                                   shell=True, verbose=True)
        logging.debug("SMIT: %s" % out)

    finally:
        vmxml.sync()
