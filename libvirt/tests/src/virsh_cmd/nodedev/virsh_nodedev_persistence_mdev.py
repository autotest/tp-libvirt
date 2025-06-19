import logging
import re

from avocado.core.exceptions import TestFail
from virttest import virsh
from virttest.libvirt_xml.nodedev_xml import NodedevXML, MdevXML
from provider.vfio import ccw


LOG = logging.getLogger("avocado." + __name__)


def get_device_xml(schid):
    """
    Returns the nodedev device xml path.

    :param schid: the subchannel id for the ccw device, e.g. 0.0.0062
    """

    parent_name = "css_" + schid.replace(".", "_")
    device_xml = NodedevXML()
    device_xml['parent'] = parent_name
    mdev_xml = MdevXML()
    mdev_xml['type_id'] = 'vfio_ccw-io'
    mdev_xml['uuid'] = '8d312cf6-f92a-485c-8db8-ba9299848f46'
    device_xml.set_cap(mdev_xml)
    LOG.debug(f"NodedevXML {device_xml}")
    return device_xml.xml


def get_device_name():
    """
    Returns first defined but not started
    mdev device name.
    """

    try:
        result = virsh.nodedev_list(cap="mdev", options="--all", ignore_status=False, debug=True)
        return result.stdout.strip().splitlines()[0]
    except:
        raise TestFail("Mdev device not found.")


def check_autostart(device_name):
    """
    Check if device is configured to autostart

    :param device_name: nodedev device name
    :raises: TestFail if autostart is not configured
    """
    result = virsh.nodedev_info(device_name, ignore_status=False, debug=True)
    if not re.findall("Autostart.*yes", result.stdout_text):
        raise TestFail("Device %s not configured to autostart." % device_name)


def run(test, params, env):
    """
    Round trip for persistent setup via nodedev API:
    define, set autostart, start, destroy, undefine

    The test assumes no other mediated device is available
    in the test environment.

    A typical node device xml would look like:
    <device>
        <parent>css_0_0_0062</parent>
            <capability type="mdev">
                <type id="vfio_ccw-io"/>
                <uuid>8d312cf6-f92a-485c-8db8-ba9299848f46</uuid>
            </capability>
    </device>
    """

    schid = None
    devid = params.get("devid")

    try:
        schid, _ = ccw.get_device_info(devid)
        ccw.set_override(schid)
        nodedev_file_path = get_device_xml(schid)
        virsh.nodedev_define(nodedev_file_path, ignore_status=False, debug=True)
        device_name = get_device_name()
        virsh.nodedev_autostart(device_name, ignore_status=False, debug=True)
        check_autostart(device_name)
        virsh.nodedev_start(device_name, ignore_status=False, debug=True)
        virsh.nodedev_destroy(device_name, ignore_status=False, debug=True)
        virsh.nodedev_undefine(device_name, ignore_status=False, debug=True)
    finally:
        if schid:
            ccw.unset_override(schid)
