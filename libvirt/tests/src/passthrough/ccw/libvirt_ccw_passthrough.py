import logging

from uuid import uuid4

from avocado.core.exceptions import TestError

from virttest import utils_package
from virttest import virsh
from virttest.utils_zchannels import SubchannelPaths, ChannelPaths
from virttest.utils_misc import cmd_status_output
from virttest.libvirt_xml.devices.hostdev import Hostdev
from virttest.libvirt_xml.vm_xml import VMXML


def device_is_listed(session, chpids):
    """
    Checks if the css device is listed by comparing the channel
    path ids.

    :param session: guest console session
    :param chipds: chpids where the disk is connected, e.g. "11122122"
    """

    paths = SubchannelPaths(session)
    paths.get_info()
    devices_inside_guest = [x for x in paths.devices
                            if x[paths.HEADER["CHPIDs"]] == chpids]
    return len(devices_inside_guest) > 0


def set_override(schid):
    """
    Sets the driver override for the device' subchannel.

    :param schid: Subchannel path id for device.
    """

    cmd = "driverctl -b css set-override %s vfio_ccw" % schid
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        raise TestError("Can't set driver override. %s" % out)


def unset_override(schid):
    """
    Unsets the driver override for the device' subchannel.

    :param schid: Subchannel path id for device.
    """

    cmd = "driverctl -b css unset-override %s" % schid
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        raise TestError("Can't set driver override. %s" % out)


def start_device(uuid, schid):
    """
    Starts the mdev with mdevctl

    :param uuid: device uuid
    :param schid: subchannel id for the device
    """

    cmd = "mdevctl start -u %s -p %s -t vfio_ccw-io" % (uuid, schid)
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        raise TestError("Can't start mdev. %s" % out)


def stop_device(uuid):
    """
    Stops the mdev with mdevctl

    :param uuid: device uuid
    """

    cmd = "mdevctl stop -u %s" % (uuid)
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        logging.warning("Couldn't stop device. %s", out)


def attach_hostdev(vm_name, uuid):
    """
    Attaches the mdev to the machine.

    :param vm_name: VM name
    :param uuid: mdev uuid
    """

    hostdev_xml = Hostdev()
    hostdev_xml.mode = "subsystem"
    hostdev_xml.model = "vfio-ccw"
    hostdev_xml.type = "mdev"
    hostdev_xml.source = hostdev_xml.new_source(**{"uuid": uuid})
    hostdev_xml.xmltreefile.write()
    virsh.attach_device(vm_name, hostdev_xml.xml, flagstr="--current",
                        ignore_status=False)


def assure_preconditions():
    """
    Makes sure that preconditions are established.
    """

    utils_package.package_install(["mdevctl",
                                   "driverctl"])


def get_device_info():
    """
    Gets the device info for passthrough
    """

    paths = SubchannelPaths()
    paths.get_info()
    device = paths.get_first_unused_and_safely_removable()
    schid = device[paths.HEADER["Subchan."]]
    chpids = device[paths.HEADER["CHPIDs"]]
    return schid, chpids


def run(test, params, env):
    """
    Test for CCW, esp. DASD disk passthrough on s390x.

    The CCW disk/its subchannel for passthrough is expected to
    be listed on the host but not enabled for use.
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()

    device_removal_case = "yes" == params.get("device_removal_case", "no")
    schid = None
    uuid = None
    chpids = None

    try:
        assure_preconditions()

        if vm.is_alive():
            vm.destroy()

        schid, chpids = get_device_info()
        uuid = str(uuid4())

        set_override(schid)
        start_device(uuid, schid)
        attach_hostdev(vm_name, uuid)

        vm.start()
        session = vm.wait_for_login()

        if not device_is_listed(session, chpids):
            test.fail("Device not visible inside guest")

        if device_removal_case:
            ChannelPaths.set_standby(chpids)
            if device_is_listed(session, chpids):
                test.fail("Device must not be visible inside guest")

            vm.destroy()
            ChannelPaths.set_online(chpids)

            set_override(schid)
            start_device(uuid, schid)

            vm.start()
            session = vm.wait_for_login()

            if not device_is_listed(session, chpids):
                test.fail("Device not visible after restoring setup.")

    finally:
        if chpids:
            ChannelPaths.set_online(chpids)
        if uuid:
            stop_device(uuid)
        if schid:
            unset_override(schid)
        backup_xml.sync()
