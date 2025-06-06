"""
This module contains classes and functions for handling mediated devices
"""

import logging
import re
from time import sleep
from uuid import uuid4

from avocado.core.exceptions import TestError, TestFail
from virttest import virsh
from virttest.utils_misc import wait_for
from virttest.utils_zcrypt import (APMaskHelper, CryptoDeviceInfoBuilder,
                                   MatrixDevice, load_vfio_ap, unload_vfio_ap)

from provider.vfio import ccw, get_nodedev_xml, get_parent_device

LOG = logging.getLogger("avocado." + __name__)


class MdevHandler(object):
    """Base class for mdev type specific implementations"""

    def create_nodedev(self, api="nodedev"):
        """
        Creates the mdev and returns its name

        :param api: The name of the API to use. Mediated devices can
                    be created e.g. via:
                        - sysfs
                        - mdevctl
                        - nodedev
                    An implementer should raise a TestError if
                    a specific method is not supported.
        """
        raise NotImplementedError()

    def get_target_address(self):
        """Returns a target address to use for hostdev"""
        raise NotImplementedError()

    def check_device_present_inside_guest(self, session):
        """
        Checks if the host device is present inside the guest

        :param session: guest session
        """
        raise NotImplementedError()

    def clean_up(self):
        """Stops the mediated device and returns resources to the host"""
        raise NotImplementedError()

    @staticmethod
    def from_type(mdev_type):
        """
        Creates implementing instance for mdev_type

        :param mdev_type: The mediated device type as by nodedev API
        """
        if mdev_type == "vfio_ccw-io":
            return CcwMdevHandler()
        if mdev_type == "vfio_ap-passthrough":
            return ApMdevHandler()
        else:
            raise TestError("Test doesn't know how to handle %s." % mdev_type)


def get_first_mdev_nodedev_name():
    """
    Returns the first nodedev of type mdev known to libvirt

    :return: the first listed mdev node device
    """
    result = virsh.nodedev_list(cap="mdev", debug=True)
    device_names = result.stdout.strip().splitlines()
    if result.exit_status or len(device_names) == 0:
        raise TestError(
            "Couldn't create nodedev. %s. %s." % (result.stderr, result.stdout)
        )
    return device_names[0]


class CcwMdevHandler(MdevHandler):
    """Class implementing test methods for vfio_ccw-io"""

    def __init__(self):
        self.uuid = str(uuid4())
        self.chpids = None
        self.schid = None
        self.target_address = None
        self.expected_device_address = None
        self.device_id = None
        self.nodedev_pattern = r"mdev.*\d{4}"
        self.session = None
        self.parent = None

    def create_nodedev(self, api="mdevctl", devid=None):
        """
        Creates a mediated device of a specific type
        and returns its name from libvirt.

        :return: name of mdev device as node device
        """
        previously_run = self.schid is not None
        if not previously_run:
            self.schid, self.chpids = ccw.get_device_info(devid)
            self.parent = get_parent_device("ccw_" + devid.replace(".", "_"))
            ccw.set_override(self.schid)

        if api == "mdevctl":
            return self._mdevctl_start()
        elif api == "nodedev":
            device_xml = get_nodedev_xml("vfio_ccw-io", self.parent, self.uuid)
            return self._nodedev_create(device_xml.xml)
        else:
            raise TestError("Handling mdev via '%s' is not implemented." % api)

    def _mdevctl_start(self):
        ccw.start_device(self.uuid, self.schid)

        return get_first_mdev_nodedev_name()

    def _nodedev_create(self, xml):
        res = virsh.nodedev_create(xml, debug=True, ignore_status=False)
        o = res.stdout_text
        return re.search(self.nodedev_pattern, o)[0]

    def get_target_address(self):
        """
        Returns a valid target device address

        :return: hostdev target address
        """
        self.target_address = (
            "address.type=ccw,address.cssid=0xfe,address.ssid=0x0,address.devno=0x1111"
        )
        self.expected_device_address = "0.0.1111"
        return self.target_address

    def check_device_present_inside_guest(self, session):
        """
        Fails the test if the device can't be found inside the guest.

        :param session: guest session
        :raises: TestFail if device not found
        """
        self.session = session
        paths = ccw.get_subchannel_info(session)
        found_devices = [
            x
            for x in paths.devices
            if x[paths.HEADER["Device"]] == self.expected_device_address
        ]
        if not found_devices:
            raise TestFail(
                "Couldn't find device inside guest."
                "Expected address %s, found %s."
                % (self.expected_device_address, paths.devices)
            )
        LOG.debug(
            "Device was found inside guest with" " expected id %s." % found_devices[0]
        )

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


class ApMdevHandler(MdevHandler):
    """Class implementing test methods for vfio_ap-passthrough"""

    def __init__(self):
        self.uuid = str(uuid4())
        self.mask_helper = None
        self.matrix_dev = None
        self.session = None
        self.devices = None
        self.nodedev_pattern = "mdev.*matrix"
        # minimal supported hwtype
        self.MIN_HWTYPE = 10
        self.vfio_ap_loaded = None

    def create_nodedev(self, api="sysfs", domains=[]):
        """
        Creates a mediated device of a specific type
        and returns its name from libvirt.

        :return: name of mdev device as node device
        """
        previously_run = self.devices is not None
        if not previously_run:
            load_vfio_ap()
            self.vfio_ap_loaded = True

            info = CryptoDeviceInfoBuilder.get()
            LOG.debug("Host lszcrypt got %s", info)

            if not info.entries or int(info.domains[0].hwtype) < self.MIN_HWTYPE:
                raise TestError(
                    "vfio-ap requires at least HWTYPE %s." % self.MIN_HWTYPE
                )

            if not domains:
                self.devices = [info.domains[0]]
            else:
                self.devices = [x for x in info.domains if ".".join(x.id) in domains]
            self.mask_helper = APMaskHelper.from_infos(self.devices)

        if api == "sysfs":
            self.matrix_dev = MatrixDevice.from_infos(self.devices)
            return get_first_mdev_nodedev_name()
        elif api == "nodedev":
            device_xml = get_nodedev_xml(
                "vfio_ap-passthrough", "ap_matrix", self.uuid, domains
            )
            return self._nodedev_create(device_xml.xml)
        else:
            raise TestError("Handling mdev via '%s' is not implemented." % api)

    def _nodedev_create(self, xml):
        res = virsh.nodedev_create(xml, debug=True, ignore_status=False)
        o = res.stdout_text
        return re.search(self.nodedev_pattern, o)[0]

    def create_blank_nodedev(self):
        """
        Creates a mediated device for vfio_ap but doesn't assign
        anything to the matrix yet.
        """

        load_vfio_ap()
        self.vfio_ap_loaded = True
        info = CryptoDeviceInfoBuilder.get()
        LOG.debug("Host lszcrypt got %s", info)

        self.devices = info.domains
        self.mask_helper = APMaskHelper.from_infos(self.devices)

        self.matrix_dev = MatrixDevice()

    def get_target_address(self):
        """
        Returns a valid target device address

        :return: hostdev target address
        """
        # AP devices don't have the target address //hostdev/address
        return None

    def check_device_present_inside_guest(self, session):
        """
        Fails the test if the device can't be found inside the guest.

        :param session: guest session
        :raises: TestFail if device not found
        """
        self.session = session

        def verify_passed_through():
            guest_info = CryptoDeviceInfoBuilder.get(session)
            LOG.debug("Guest lszcrypt got %s", guest_info)
            if guest_info.domains:
                default_driver_on_host = self.devices[0].driver
                driver_in_guest = guest_info.domains[0].driver
                LOG.debug(
                    "Expecting default drivers from host and guest"
                    " to be the same: { host: %s, guest: %s }",
                    default_driver_on_host,
                    driver_in_guest,
                )
                return default_driver_on_host == driver_in_guest
            return False

        if not wait_for(verify_passed_through, timeout=60, step=10):
            raise TestFail(
                "Crypto domain not attached correctly in guest."
                " Please, check the test log for details."
            )

    def clean_up(self):
        """
        Returns the mdev resources to the host.
        """
        if self.session:
            self.session.close()
        if self.matrix_dev:
            self.matrix_dev.unassign_all()
        if self.mask_helper:
            self.mask_helper.return_to_host_all()
        if self.vfio_ap_loaded:
            unload_vfio_ap()
