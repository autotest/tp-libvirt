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
import logging

from avocado.core.exceptions import TestFail

from virttest.libvirt_xml.vm_xml import VMXML
from virttest.utils_misc import cmd_status_output, wait_for
from virttest.utils_zcrypt import load_vfio_ap

from provider.vfio import ap
from provider.vfio.mdev_handlers import MdevHandler

LOG = logging.getLogger("avocado." + __name__)


def confirm_device_is_running(uuid, session=None):
    """
    Confirm that a mediated device is running.

    :param uuid: The UUID of the mediated device.
    :param session: A guest session. If not None, the command will
                    be executed on the host.
    :raises TestFail: if the device isn't running.
    """
    def _is_listed():
        """Parameterless helper function to use with wait_for"""
        cmd = "mdevctl list -u %s" % uuid
        err, out = cmd_status_output(cmd, shell=True, session=session)
        LOG.debug(err, out)
        return uuid in out
    if not wait_for(_is_listed, timeout=5):
        raise TestFail("Mediated device UUID(%s) not listed" % uuid)


def run(test, params, env):
    """
    Tests that vfio-ap passthrough configurations can autostart

    1. Pass device through into guest
    2. Inside guest create a persistent autostart configuration
    3. Reboot the guest and confirm the device is running
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml_backup = VMXML.new_from_inactive_dumpxml(vm_name)

    handler = None

    try:
        handler = MdevHandler.from_type("vfio_ap-passthrough")

        handler.create_nodedev()
        ap.attach_hostdev(vm_name, handler.matrix_dev.uuid)
        vm.start()

        session = vm.wait_for_login()

        load_vfio_ap(session)
        domain_info = ".".join([handler.devices[0].card,
                                handler.devices[0].domain])
        uuid = ap.create_autostart_mediated_device(domain_info, session)
        confirm_device_is_running(uuid, session)
        session.close()

        vm.reboot()
        session = vm.wait_for_login()
        confirm_device_is_running(uuid, session)
        session.close()
    finally:
        vmxml_backup.sync()
        if handler:
            handler.clean_up()
