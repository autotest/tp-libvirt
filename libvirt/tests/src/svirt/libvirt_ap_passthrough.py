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

import logging

from virttest import virsh
from virttest.utils_zcrypt import CryptoDeviceInfoBuilder, \
    APMaskHelper, MatrixDevice, load_vfio_ap, unload_vfio_ap
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices import hostdev
from virttest.utils_misc import wait_for

# minimal supported hwtype
MIN_HWTYPE = 10


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
    mask_helper = None
    matrix_dev = None

    try:
        if plug == "cold" and vm.is_alive():
            vm.destroy()
        if plug == "hot" and vm.is_dead():
            vm.start()
            vm.wait_for_login()

        load_vfio_ap()

        info = CryptoDeviceInfoBuilder.get()

        if not info.entries or int(info.domains[0].hwtype) < MIN_HWTYPE:
            test.error("vfio-ap requires at least HWTYPE %s." % MIN_HWTYPE)

        devices = [info.domains[0]]
        mask_helper = APMaskHelper.from_infos(devices)
        matrix_dev = MatrixDevice.from_infos(devices)

        hostdev_xml = hostdev.Hostdev()
        hostdev_xml.mode = "subsystem"
        hostdev_xml.model = "vfio-ap"
        hostdev_xml.type = "mdev"
        uuid = matrix_dev.uuid
        hostdev_xml.source = hostdev_xml.new_source(**{"uuid": uuid})
        hostdev_xml.xmltreefile.write()

        logging.debug("Attaching %s", hostdev_xml.xmltreefile)
        virsh.attach_device(vm_name, hostdev_xml.xml, flagstr="--current",
                            ignore_status=False)

        if plug == "cold":
            vm.start()

        session = vm.wait_for_login()

        def verify_passed_through():
            guest_info = CryptoDeviceInfoBuilder.get(session)
            logging.debug("Guest lszcrypt got %s", guest_info)
            if guest_info.domains:
                default_driver_on_host = devices[0].driver
                driver_in_guest = guest_info.domains[0].driver
                logging.debug("Expecting default drivers from host and guest"
                              " to be the same: { host: %s, guest: %s }",
                              default_driver_on_host, driver_in_guest)
                return default_driver_on_host == driver_in_guest
            return False

        if not wait_for(verify_passed_through, timeout=60, step=10):
            test.fail("Crypto domain not attached correctly in guest."
                      " Please, check the test log for details.")
    finally:
        vmxml_backup.sync()
        if matrix_dev:
            matrix_dev.unassign_all()
        if mask_helper:
            mask_helper.return_to_host_all()
        unload_vfio_ap()
