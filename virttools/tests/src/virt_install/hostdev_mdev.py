import logging

from time import sleep
from uuid import uuid4
from avocado.core.exceptions import TestError
from avocado.core.exceptions import TestFail
from provider.vfio import ccw
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_misc import cmd_status_output
from virttest import virsh

LOG = logging.getLogger('avocado.' + __name__)


class MdevHandler(object):
    """ Base class for mdev type specific implementations """

    def create_nodedev(self):
        """ Creates the mdev and returns its name """
        raise NotImplementedError()

    def get_target_address(self):
        """ Returns a target address to use for hostdev """
        raise NotImplementedError()

    def check_device_present_inside_guest(self, session):
        """
        Checks if the host device is present inside the guest

        :param session: guest session
        """
        raise NotImplementedError()

    def clean_up(self):
        """ Stops the mediated device and returns resources to the host """
        raise NotImplementedError()

    @staticmethod
    def from_type(mdev_type):
        """
        Creates implementing instance for mdev_type

        :param mdev_type: The mediated device type as by nodedev API
        """
        if mdev_type == "vfio_ccw-io":
            return CcwMdevHandler()
        else:
            raise TestError("Test doesn't know how to handle %s." % mdev_type)


class CcwMdevHandler(MdevHandler):
    """ Class implementing test methods for vfio_ccw-io """

    def __init__(self):
        self.uuid = None
        self.chpids = None
        self.schid = None
        self.target_address = None
        self.expected_device_address = None
        self.device_id = None
        self.session = None

    def create_nodedev(self):
        """
        Creates a mediated device of a specific type
        and returns its name from libvirt.

        :return: name of mdev device as node device
        """
        self.schid, self.chpids = ccw.get_device_info()
        self.device_id, _ = ccw.get_first_device_identifiers(self.chpids, None)
        ccw.set_override(self.schid)
        self.uuid = str(uuid4())
        ccw.start_device(self.uuid, self.schid)

        return get_first_mdev_nodedev_name()

    def get_target_address(self):
        """
        Returns a valid target device address

        :return: hostdev target address
        """
        self.target_address = "address.type=ccw,address.cssid=0xfe,address.ssid=0x0,address.devno=0x1111"
        self.expected_device_address = "0.0.1111"
        return self.target_address

    def check_device_present_inside_guest(self, session):
        """
        Fails the test if the device can't be found inside the guest.

        :param session: guest session
        :raises: TestFail if device not found
        """
        self.session = session
        device, _ = ccw.get_first_device_identifiers(self.chpids, session)
        if device != self.expected_device_address:
            raise TestFail("Couldn't find device inside guest."
                           "Expected address %s, found %s." %
                           (self.expected_device_address, device))
        LOG.debug("Device was found inside guest with"
                  " expected id %s." % device)

    def clean_up(self):
        """
        Returns the mdev resources to the host.
        """
        if self.session:
            self.session.close()
        if self.uuid:
            ccw.stop_device(self.uuid)
        if self.schid:
            ccw.unset_override(self.schid)
            # need to sleep to avoid issue with setting device offline
            # adding a wait_for would likely be more complicated
            sleep(1)
        if self.device_id:
            ccw.set_device_offline(self.device_id)


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
