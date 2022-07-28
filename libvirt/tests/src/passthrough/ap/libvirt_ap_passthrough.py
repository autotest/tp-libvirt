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
# Copyright: Red Hat Inc. 2020
# Author: Sebastian Mitterle <smitterl@redhat.com>
from virttest.libvirt_xml.vm_xml import VMXML

from provider.vfio.mdev_handlers import MdevHandler
from provider.vfio import ap


def run(test, params, env):
    """
    Tests vfio-ap passthrough on s390x

    1. Control guest lifecycle for cold- vs. hotplug
    2. Set up passthrough attaching new device
    3. Confirm device availability in guest
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    vmxml_backup = VMXML.new_from_inactive_dumpxml(vm_name)

    plug = params.get("plug")
    handler = None

    try:
        handler = MdevHandler.from_type("vfio_ap-passthrough")
        if plug == "cold" and vm.is_alive():
            vm.destroy()
        if plug == "hot" and vm.is_dead():
            vm.start()
            vm.wait_for_login()

        handler.create_nodedev()

        ap.attach_hostdev(vm_name, handler.matrix_dev.uuid)

        if plug == "cold":
            vm.start()

        session = vm.wait_for_login()

        handler.check_device_present_inside_guest(session)

    finally:
        vmxml_backup.sync()
        if handler:
            handler.clean_up()
