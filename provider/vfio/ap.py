import logging as log
from uuid import uuid4

from avocado.core.exceptions import TestError
from virttest import virsh
from virttest.libvirt_xml.devices import hostdev
from virttest.utils_misc import cmd_status_output

LOG = log.getLogger('avocado.' + __name__)


def attach_hostdev(vm_name, uuid):
    """
    Prepare device XML and attach it to the VM
    in its current state

    :param vm_name: The name of the VM
    :param uuid: The UUID of the mediated device
    """

    hostdev_xml = hostdev.Hostdev()
    hostdev_dict = {
                    'type_name': 'mdev',
                    'model': 'vfio-ap',
                    'mode': 'subsystem',
                    'type': 'mdev',
                    'source': {
                                'untyped_address': {'uuid': uuid}}}
    hostdev_xml.setup_attrs(**hostdev_dict)
    hostdev_xml.xmltreefile.write()
    LOG.debug("Attaching %s", hostdev_xml.xmltreefile)
    virsh.attach_device(vm_name, hostdev_xml.xml, flagstr="--current",
                        ignore_status=False)


def create_autostart_mediated_device(domain_info, session=None):
    """
    Creates the full persistent device configuration, using mdevctl,
    sets it autostart and starts it.

    :param domain_info: The crypto domain identifier as listed by lszcrypt
                        CARD.DOMAIN e.g. 02.002b
    :param session: If given run the commands in the VM session
    :return uuid: The mediated device' UUID
    """
    return create_mediated_device(domain_info, session=session, start="-a")


def create_mediated_device(domain_info, session=None, start="-m"):
    """
    Creates the full persistent device configuration, using mdevctl,
    sets its start method and starts it.

    :param domain_info: The crypto domain identifier as listed by lszcrypt
                        CARD.DOMAIN e.g. 02.002b
    :param session: If given run the commands in the VM session
    :param start: device' start method: "-m": manual, "-a" autostart
    :return uuid: The mediated device' UUID
    """
    card_domain = domain_info.split(".")

    card_domain_10 = [int(x, 16) for x in card_domain]
    cmd = "chzdev -t ap apmask=-%s aqmask=-%s" % tuple(card_domain_10)
    err, out = cmd_status_output(cmd, shell=True, session=session)
    if err:
        raise TestError("Couldn't set device assignment: %s" % out)
    _, out = cmd_status_output("lszdev -t ap", shell=True, session=session)
    LOG.debug(out)

    uuid = str(uuid4())
    card_domain_16 = ["0x%s" % x for x in card_domain]
    cmds = ["mdevctl define -p matrix -t vfio_ap-passthrough -u %s" % uuid,
            "mdevctl modify -u %s %s" % (uuid, start),
            ("mdevctl modify -u %s"
             " --addattr assign_adapter --value %s" % (uuid, card_domain_16[0])),
            ("mdevctl modify -u %s"
             " --addattr assign_domain --value %s" % (uuid, card_domain_16[1])),
            "mdevctl start -u %s" % uuid]
    for cmd in cmds:
        err, out = cmd_status_output(cmd, shell=True, session=session)
        if err:
            raise TestError("Couldn't configure mediated device: %s" % out)
    return uuid


def set_crypto_device_refresh_interval(session=None, interval=5):
    """
    Set the crypto device refresh interval

    :param session: If not None, the command will be executed in a guest
    :param interval: Interval in seconds for refreshing the crypto device info
    """

    cmd = "chzcrypt -c %s" % interval
    err, out = cmd_status_output(cmd, shell=True, session=session)
    if err:
        raise TestError("Couldn't set crypto refresh interval: %s" % out)
    return
