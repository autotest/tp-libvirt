# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright: Red Hat Inc. 2022
# Author: Sebastian Mitterle <smitterl@redhat.com>
import logging as log
from time import sleep

from avocado.core.exceptions import TestError, TestFail
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_misc import cmd_status_output
from virttest.utils_zcrypt import load_vfio_ap

from provider.vfio.mdev_handlers import MdevHandler
from provider.vfio import ap

REFRESH_INTERVAL_SEC = 5


logging = log.getLogger("avocado." + __name__)


def create_mdev(session, domain_info):
    """
    Loads the vfio_ap and creates a mediated device
    with the first available domain set for passthrough

    :param session: guest session
    :param domain_info: CARD.DOMAIN id of the crypto device
    """
    load_vfio_ap(session)
    return ap.create_mediated_device(domain_info, session=session)


def wait_for_refresh():
    """
    Sleep for the time needed for the crypto device info
    to be refreshed in the guest.
    """
    sleep(REFRESH_INTERVAL_SEC)


def make_device_available(domain_info, uuid):
    """
    On the host assigns adapter and domain so that it becomes
    available in the guest
    :param domain_info: CARD.DOMAIN to be assigned
    :param uuid: The mediated device' UUID
    """

    path = get_mediated_device_path(uuid)
    adapter, domain = domain_info.split(".")
    cmds = ["echo 0x%s > %s/assign_adapter" % (adapter, path),
            "echo 0x%s > %s/assign_domain" % (domain, path),
            "echo 0x%s > %s/assign_control_domain" % (domain, path)]
    for cmd in cmds:
        err, out = cmd_status_output(cmd, shell=True, verbose=True)
        if err:
            raise TestError("Couldn't set attribute: %s" % out)
    return


def make_device_unavailable(domain_info, uuid):
    """
    On the host unassign adapter and domain so that it becomes
    unavailable in the guest
    :param domain_info: CARD.DOMAIN to be assigned
    :param uuid: The mediated device' UUID
    """

    path = get_mediated_device_path(uuid)
    adapter, domain = domain_info.split(".")
    cmds = ["echo 0x%s > %s/unassign_adapter" % (adapter, path),
            "echo 0x%s > %s/unassign_domain" % (domain, path),
            "echo 0x%s > %s/unassign_control_domain" % (domain, path)]
    for cmd in cmds:
        err, out = cmd_status_output(cmd, shell=True, verbose=True)
        if err:
            raise TestError("Couldn't set attribute: %s" % out)
    return


def get_mediated_device_path(uuid):
    """
    Returns the mediated device path in the sysfs

    :param uuid: The mediated device' UUID
    """
    return "/sys/devices/vfio_ap/matrix/%s" % uuid


def assert_guest_matrix_is(session, uuid, expected=""):
    """
    Reads the guest matrix in the mediated device and fails if it's not as expected
    :param session: If given, we assert in the guest
    :param uuid: The UUID of the mediated device
    :param expected: The expected attribute content as string
    """

    path = "%s/guest_matrix" % get_mediated_device_path(uuid)
    cmd = "cat %s" % path
    err, out = cmd_status_output(cmd, shell=True, session=session, verbose=True)
    if err:
        raise TestError("Couldn't read path %s: %s" % (path, out))
    actual = out.strip()
    if expected != actual:
        raise TestFail("Expected: %s\nGot: %s" % (expected, actual))


def run(test, params, env):
    """
    Verify that passthrough can be configured
    for vfio_ap and device availability is updated
    depending on the availability of crypto devices.
    Both, host and guest kernel need to support crypto
    device hotplug for vfio_ap for this test.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml_backup = VMXML.new_from_inactive_dumpxml(vm_name)

    handler = None
    session = None

    try:
        host_handler = MdevHandler.from_type("vfio_ap-passthrough")
        host_handler.create_blank_nodedev()
        host_uuid = host_handler.matrix_dev.uuid

        entry = [x for x in host_handler.devices if x.domain][0]
        domain_info = ".".join([entry.card, entry.domain])

        ap.attach_hostdev(vm_name, host_handler.matrix_dev.uuid)
        vm.start()
        session = vm.wait_for_login()

        ap.set_crypto_device_refresh_interval(session, REFRESH_INTERVAL_SEC)

        guest_uuid = create_mdev(session, domain_info)
        assert_guest_matrix_is(session, guest_uuid, expected="")

        make_device_available(domain_info, host_uuid)
        wait_for_refresh()
        assert_guest_matrix_is(session, guest_uuid, expected=domain_info)

        make_device_unavailable(domain_info, host_uuid)
        wait_for_refresh()
        assert_guest_matrix_is(session, guest_uuid, expected="")

    finally:
        if session:
            session.close()
        vmxml_backup.sync()
        if host_handler:
            host_handler.clean_up()
