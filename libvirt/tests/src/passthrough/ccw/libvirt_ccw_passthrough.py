from uuid import uuid4

from virttest.utils_misc import wait_for, cmd_status_output
from virttest.utils_zchannels import ChannelPaths
from virttest.libvirt_xml.vm_xml import VMXML

from provider.vfio import ccw


def mdev_listed(uuid):
    """
    Returns a function that will check if the mediated device
    with given uuid is listed

    :param uuid: uuid of the mediated device
    """

    def _mdev_listed():
        """
        True if uuid is listed, False else
        """
        cmd = "lsmdev"
        _, o = cmd_status_output(cmd)
        LOG.debug(o)
        return uuid in o

    return _mdev_listed


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
    devid = params.get("devid")
    schid = None
    uuid = None
    chpids = None

    try:
        ccw.assure_preconditions()

        if vm.is_alive():
            vm.destroy()

        schid, chpids = ccw.get_device_info(devid)
        uuid = str(uuid4())

        ccw.set_override(schid)
        ccw.start_device(uuid, schid)
        ccw.attach_hostdev(vm_name, uuid)

        vm.start()
        session = vm.wait_for_login()

        if not ccw.device_is_listed(session, chpids):
            test.fail("Device not visible inside guest")

        if device_removal_case:
            ChannelPaths.set_standby(chpids)
            if ccw.device_is_listed(session, chpids):
                test.fail("Device must not be visible inside guest")

            vm.destroy()
            ChannelPaths.set_online(chpids)

            ccw.set_override(schid)
            ccw.start_device(uuid, schid)
            wait_for(lambda: mdev_listed(uuid), timeout=10)
            vm.start()
            session = vm.wait_for_login()

            if not ccw.device_is_listed(session, chpids):
                test.fail("Device not visible after restoring setup.")

    finally:
        if vm.is_alive():
            vm.destroy()
        if chpids and device_removal_case:
            ChannelPaths.set_online(chpids)
        if uuid:
            ccw.stop_device(uuid)
        if schid:
            ccw.unset_override(schid)
        backup_xml.sync()
