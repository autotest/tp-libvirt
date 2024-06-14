import logging as log

from avocado.core.exceptions import TestError
from virttest import utils_package, virsh
from virttest.utils_misc import cmd_status_output
from virttest.utils_test.libvirt import mkfs
from virttest.utils_zchannels import SubchannelPaths

from provider.vfio import get_hostdev_xml

# default disk paths supposing only one DASD is passed through
DASD_DISK = "/dev/dasda"
DASD_PART = "/dev/dasda1"
# default mount path inside guest
MOUNT = "/mnt"


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger("avocado." + __name__)


def read_write_operations_work(session, chpids, makefs=True):
    """
    Mounts device inside guest, identified by the chpids,
    and runs some write/read operation.

    The device is expected to be the only passed through device.
    Per default the device gets a new filesystem setup.

    :param session: logged in guest session
    :param chpids: string representing CHPIDs, e.g. 11122122
    :param makefs: if False, the device is expected to have a valid
                   filesystem already
    :return: True on success
    """
    device_id, _ = get_first_device_identifiers(chpids, session)
    set_device_online(device_id, session)
    if makefs:
        make_dasd_fs(DASD_DISK, DASD_PART, session)
    mount(session)
    try:
        read_write(session)
    except:
        raise
    finally:
        umount(session)
    return True


def read_write(session):
    """
    Writes, flushes and reads test file

    :param session: guest session
    :raises TestError: if read/write operation fails
    """

    TESTFILE = "%s/testfile" % MOUNT
    cmd1 = "echo kaixo > %s" % TESTFILE
    cmd2 = "sync %s" % TESTFILE
    cmd3 = "cat %s" % TESTFILE
    err, out = None, None
    for cmd in [cmd1, cmd2, cmd3]:
        err, out = cmd_status_output(cmd, shell=True, session=session)
        if err:
            raise TestError("Some read/write operation failed. %s" % out)
        if cmd == cmd3 and "kaixo" not in out:
            raise TestError(
                "Didn't get the written value '%s'" " from file '%s'" % ("kaixo", out)
            )


def make_dasd_part(path, session):
    """
    Creates a partition spanning full disk on path

    :param path: dasd disk path, e.g. /dev/dasda
    :param session: guest session
    :return: True if partitioning succeeded
    """

    cmd = "fdasd -a %s" % path
    err, out = cmd_status_output(cmd, shell=True, session=session)
    if err:
        raise TestError("Couldn't create partition. Status code '%s'. %s." % (err, out))
    return True


def make_dasd_fs(path, part, session):
    """
    Erases disk and creates new partition with filesystem

    :param path: the disk path, e.g. /dev/dasda
    :param part: partition path, e.g. /dev/dasda1
    :param session: guest session
    """

    format_dasd(path, session)
    make_dasd_part(path, session)
    mkfs(part, "ext3", session=session)


def format_dasd(path, session):
    """
    Formats dasd disk and creates filesystem on path

    :param path: dasd disk path, e.g. /dev/dasda
    :param session: guest session
    :raises TestError: if disk can't be formatted
    :return: True if formatting succeeded
    """

    cmd = "dasdfmt -b 4096 -M quick --force -p -y %s" % path
    err, out = cmd_status_output(cmd, shell=True, session=session)
    if err:
        raise TestError("Couldn't format disk. %s" % out)
    return True


def umount(session):
    """
    Unmount dasd partition inside guest

    :param session: guest session
    :raises TestError: if partition can't be unmounted
    """

    cmd = "umount %s" % MOUNT
    err, out = cmd_status_output(cmd, shell=True, session=session)
    if err:
        raise TestError("Couldn't umount partition. %s" % out)


def mount(session):
    """
    Mount dasd partition inside guest

    :param session: guest session
    :raises TestError: if the partition can't be mounted
    """

    cmd = "mount %s %s" % (DASD_PART, MOUNT)
    err, out = cmd_status_output(cmd, shell=True, session=session)
    if err:
        raise TestError("Couldn't mount partition. %s" % out)


def set_device_offline(device_id, session=None):
    """
    Sets device offline

    :param device_id: cssid.ssid.devno, e.g. 0.0.560a
    :param session: guest session, command is run on host if None
    :raises TestError: if the device can't be set offline
    """

    cmd = "chccwdev -d %s" % device_id
    err, out = cmd_status_output(cmd, shell=True, session=session)
    if err:
        raise TestError("Could not set device offline. %s" % out)


def set_device_online(device_id, session=None):
    """
    Sets device online

    :param device_id: cssid.ssid.devno, e.g. 0.0.560a
    :param session: guest session, command is run on host if None
    :raises TestError: if the device can't be set online
    """

    cmd = "chccwdev -e %s" % device_id
    err, out = cmd_status_output(cmd, shell=True, session=session)
    if err:
        raise TestError("Could not set device online. %s" % out)


def get_first_device_identifiers(chpids, session):
    """
    Gets the usual device identifier cssid.ssid.devno

    :param chpids: chpids where the disk is connected, e.g. "11122122"
    :param session: guest session
    :return: Pair of strings, "cssid.ssid.devno" "cssid.ssid.schid"
    :raises TestError: if the device can't be found inside guest
    """

    paths = SubchannelPaths(session)
    paths.get_info()
    devices_inside_guest = [
        x for x in paths.devices if x[paths.HEADER["CHPIDs"]] == chpids
    ]
    if not devices_inside_guest:
        raise TestError("Device with chpids %s wasn't" " found inside guest" % chpids)
    first = devices_inside_guest[0]
    return first[paths.HEADER["Device"]], first[paths.HEADER["Subchan."]]


def device_is_listed(session, chpids):
    """
    Checks if the css device is listed by comparing the channel
    path ids.

    :param session: guest console session
    :param chpids: chpids where the disk is connected, e.g. "11122122"
    :return: True if device is listed
    """

    paths = SubchannelPaths(session)
    paths.get_info()
    devices_inside_guest = [
        x for x in paths.devices if x[paths.HEADER["CHPIDs"]] == chpids
    ]
    return len(devices_inside_guest) > 0


def set_override(schid):
    """
    Sets the driver override for the device' subchannel.

    :param schid: Subchannel path id for device.
    :raises TestError: if override can't be set
    """

    cmd = "driverctl -b css set-override %s vfio_ccw" % schid
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        raise TestError("Can't set driver override. %s" % out)


def unset_override(schid):
    """
    Unsets the driver override for the device' subchannel.

    :param schid: Subchannel path id for device.
    :raises TestError: if override can't be unset
    """

    cmd = "driverctl -b css unset-override %s" % schid
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        raise TestError("Can't unset driver override. %s" % out)


def start_device(uuid, schid):
    """
    Starts the mdev with mdevctl

    :param uuid: device uuid
    :param schid: subchannel id for the device
    :raises TestError: mdev can't be started
    """

    cmd = "mdevctl start -u %s -p %s -t vfio_ccw-io" % (uuid, schid)
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        raise TestError("Can't start mdev. %s" % out)


def stop_device(uuid):
    """
    Stops the mdev with mdevctl

    :param uuid: device uuid
    :raises TestError: mdev can't be stopped
    """

    cmd = "mdevctl stop -u %s" % (uuid)
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        logging.warning("Couldn't stop device. %s", out)


def detach_hostdev(vm_name, uuid):
    """
    Detaches the mdev to the machine.

    :param vm_name: VM name
    :param uuid: mdev uuid
    """

    hostdev_xml = get_hostdev_xml(uuid, "vfio-ccw")
    virsh.detach_device(
        vm_name, hostdev_xml.xml, flagstr="--current", ignore_status=False
    )


def attach_hostdev(vm_name, uuid):
    """
    Attaches the mdev to the machine.

    :param vm_name: VM name
    :param uuid: mdev uuid
    """

    hostdev_xml = get_hostdev_xml(uuid, "vfio-ccw")
    virsh.attach_device(
        vm_name, hostdev_xml.xml, flagstr="--current", ignore_status=False
    )


def assure_preconditions():
    """
    Makes sure that preconditions are established.
    """

    utils_package.package_install(["mdevctl", "driverctl"])


def get_device_info(devid=None):
    """
    Gets the device info for passthrough.
    It selects a device that's safely removable if devid
    is not given.

    :param devid: The ccw device id, e.g. 0.0.5000
    :return: Subchannel and Channel path ids (schid, chpids)
    """

    paths = SubchannelPaths()
    paths.get_info()
    device = None
    if devid:
        device = paths.get_device(devid)
    else:
        device = paths.get_first_unused_and_safely_removable()
    schid = device[paths.HEADER["Subchan."]]
    chpids = device[paths.HEADER["CHPIDs"]]
    return schid, chpids
