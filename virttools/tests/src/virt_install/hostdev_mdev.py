import logging

from avocado.core.exceptions import TestError
from provider.vfio.mdev_handlers import MdevHandler
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_misc import cmd_status_output
from virttest import virsh

LOG = logging.getLogger('avocado.' + __name__)


def get_disk_for_import(vmxml):
    """
    Returns the absolute path to a disk image for import.
    Assume the boot image is the first disk and an image file.

    :param vmxml: VMXML instance
    :return: absolute path to the guest's first disk image file
    """
    disks = vmxml.get_disk_all()
    disk_list = list(disks.values())
    first_disk = disk_list[0]
    return first_disk.find('source').get('file')


def get_first_mdev_nodedev_name():
    """
    Returns the first nodedev of type mdev known to libvirt

    :return: the first listed mdev node device
    """
    result = virsh.nodedev_list(cap="mdev", debug=True)
    device_names = result.stdout.strip().splitlines()
    if result.exit_status or len(device_names) == 0:
        raise TestError("Couldn't create nodedev. %s. %s." %
                        (result.stderr, result.stdout))
    return device_names[0]


def virt_install_with_hostdev(vm_name, mdev_nodedev, target_address, disk_path):
    """
    Runs virt-install with hostdev

    :param vm_name: guest name
    :param mdev_nodedev: mdev name as node device
    :param target_address: hostdev target address definition
    :param disk_path: path to the disk image for import
    """
    cmd = ("virt-install --import --name %s"
           " --hostdev %s,%s"
           " --disk %s"
           " --vcpus 2 --memory 2048"
           " --osinfo detect=on,require=off"
           " --nographics --noautoconsole" %
           (vm_name, mdev_nodedev, target_address, disk_path))
    err, out = cmd_status_output(cmd, shell=True, verbose=True)
    if err:
        raise TestError("Couldn't install vm with hostdev: %s" % out)


def run(test, params, env):
    """
    Confirm that a mediated device can be used by virt-install.
    For this we import a disk we know will boot and check the
    result inside the guest.
    The mediated device is created by the test and assumed
    to be the only mediated device in the test environment.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    mdev_type = params.get("mdev_type", "vfio_ccw-io")
    handler = None

    try:

        vm.undefine()
        handler = MdevHandler.from_type(mdev_type)
        disk = get_disk_for_import(vmxml)
        mdev_nodedev = handler.create_nodedev()
        target_address = handler.get_target_address()

        virt_install_with_hostdev(vm_name, mdev_nodedev, target_address, disk)

        session = vm.wait_for_login()
        handler.check_device_present_inside_guest(session)

    finally:
        vmxml.sync()
        if handler:
            handler.clean_up()
