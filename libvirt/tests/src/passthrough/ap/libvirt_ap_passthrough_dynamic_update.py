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
# Copyright: Red Hat Inc. 2025
# Author: Sebastian Mitterle <smitterle@redhat.com>

import logging as log
import re
import time

from uuid import uuid4

from avocado.core.exceptions import TestError, TestFail

from virttest.libvirt_xml.vm_xml import VMXML
from virttest import virsh
from virttest.utils_misc import cmd_status_output
from virttest.utils_zcrypt import CryptoDeviceInfoBuilder

from provider.vfio import ap, get_nodedev_xml

logging = log.getLogger("avocado." + __name__)
cleanup_actions = []
device_uuid = None
device_uuid_2 = None
REFRESH_INTERVAL = 5
info = None
add_condition = None


def get_devices(info, cards, domains):
    """
    Get the list of devices in the cards X domains matrix
    """
    logging.debug("All devices: %s", [str(x) for x in info.domains])
    _cards = list(set([x.card for x in info.domains]))[:cards]
    _domains = list(set([x.domain for x in info.domains]))[:domains]
    devices = [x for x in info.domains if x.card in _cards and x.domain in _domains]
    logging.debug("Filtered devices: %s", [str(x) for x in devices])
    return devices


def check_host_requirements(info, params):
    """
    Check if host has sufficient crypto cards and domains available.
    Abort with TestError if requirements not met.
    """
    cards_before = int(params.get("cards_before", 1))
    cards_after = int(params.get("cards_after", 1))
    domains_before = int(params.get("domains_before", 1))
    domains_after = int(params.get("domains_after", 1))

    min_cards = max(cards_before, cards_after)
    min_domains = max(domains_before, domains_after)

    try:
        if not info.entries:
            raise TestError("No crypto devices found on host")

        # Check if we have enough cards and domains
        available_cards = set()
        available_domains = set()

        for entry in info.entries:
            if entry.card:
                available_cards.add(entry.card)
            if entry.domain:
                available_domains.add(entry.domain)

        if len(available_cards) < min_cards:
            raise TestError(
                f"Not enough crypto cards available. Required: {min_cards}, Available: {len(available_cards)}"
            )

        if len(available_domains) < min_domains:
            raise TestError(
                f"Not enough crypto domains available. Required: {min_domains}, Available: {len(available_domains)}"
            )

        if info.domains and int(info.domains[0].hwtype) < 10:
            raise TestError("vfio-ap requires at least HWTYPE 10")

        logging.info("Host requirements check passed")

    except Exception as e:
        raise TestError(f"Failed to check host requirements: {e}")


def update_crypto_device_assignment(selected_devices):
    assign_crypto_devices_to_vfio(selected_devices, num_calls=2)


def assign_crypto_devices_to_vfio(selected_devices, num_calls=1):
    """
    Assign host crypto devices to vfio_ap using chzdev -t ap command.
    """
    try:
        if num_calls == 2 and "do_not_update_host_matrix" == add_condition:
            return

        for device in selected_devices:
            # Convert hex to decimal for chzdev
            card_dec = int(device.card, 16)
            domain_dec = int(device.domain, 16)

            cmd = f"chzdev -t ap apmask=-{card_dec} aqmask=-{domain_dec}"
            err, out = cmd_status_output(cmd, shell=True)
            if err:
                raise TestError(
                    f"Failed to assign crypto device {device.card}.{device.domain}: {out}"
                )

        logging.info(
            f"Successfully assigned {len(selected_devices)} crypto devices to vfio_ap"
        )

    except Exception as e:
        raise TestError(f"Failed to assign crypto devices to vfio_ap: {e}")


def attach_hostdev_to_vm(vm_name):
    """
    Define host device XML and attach it to the running VM.
    """
    try:
        hostdev_xml = ap.attach_hostdev(vm_name, device_uuid)
        logging.info(f"Successfully attached hostdev to VM {vm_name}")

    except Exception as e:
        raise TestError(f"Failed to attach hostdev to VM: {e}")
    return hostdev_xml


def update_nodedev_xml(first_xml, flagstr, devices, test):
    """
    Update the already running device with virsh.nodedev_update.
    """
    updated_xml = get_nodedev_xml(
        "vfio_ap-passthrough",
        "ap_matrix",
        device_uuid,
        [".".join(x.id) for x in devices],
        name=first_xml.name,
    )

    if "undefine_device_before_update" == add_condition:
        virsh.nodedev_undefine(first_xml.name, debug=True, ignore_status=False)
    if "create_second_device_with_after_values" == add_condition:
        second_xml = get_nodedev_xml(
            "vfio_ap-passthrough",
            "ap_matrix",
            device_uuid_2,
            [".".join(x.id) for x in devices],
        )
        result = virsh.nodedev_create(second_xml.xml, debug=True, ignore_status=False)
        name = re.search(r"Node device ([a-z0-9_']+) created", result.stdout_text)[
            1
        ].strip("'")
        cleanup_actions.append(
            lambda: virsh.nodedev_destroy(name, debug=True, ignore_status=False)
        )

    result = virsh.nodedev_update(
        first_xml.name, updated_xml.xml, options=flagstr, debug=True, ignore_status=True
    )

    if result.exit_status and add_condition in [
        "create_second_device_with_after_values",
        "do_not_update_host_matrix",
        "undefine_device_before_update",
    ]:
        return True
    if result.exit_status:
        raise TestError(f"Failed to update running device: {result.stderr}")


def define_and_start_nodedev(devices):
    """ """
    nodedev_xml = get_nodedev_xml(
        "vfio_ap-passthrough",
        "ap_matrix",
        device_uuid,
        [".".join(x.id) for x in devices],
    )
    result = virsh.nodedev_define(nodedev_xml.xml, debug=True, ignore_status=True)
    if result.exit_status:
        raise TestError(f"Failed to define device: {result.stderr}")
    name = re.search(r"Node device ([a-z0-9_']+) defined", result.stdout_text)[1].strip(
        "'"
    )
    cleanup_actions.append(
        lambda: virsh.nodedev_undefine(name, debug=True, ignore_status=False)
    )

    result = virsh.nodedev_start(name, debug=True, ignore_status=True)
    if result.exit_status:
        raise TestError(f"Failed to start device: {result.stderr}")
    cleanup_actions.append(
        lambda: virsh.nodedev_destroy(name, debug=True, ignore_status=False)
    )
    nodedev_xml.name = name
    nodedev_xml.xmltreefile.write()
    return nodedev_xml


def verify_matrix_in_guest(session, vm, name, hostdev_xml, devices):
    """
    Confirm that the updated matrix is correct inside the guest.
    """
    if "restart_all_before_guest_verification" == add_condition:
        session.close()
        vm.destroy()
        virsh.nodedev_destroy(name, debug=True, ignore_status=False)
        virsh.nodedev_start(name, debug=True, ignore_status=False)
        vm.start()
        virsh.attach_device(vm.name, hostdev_xml.xml, debug=True, ignore_status=False)
        session = vm.wait_for_login()

    session.cmd(f"chzcrypt -c {REFRESH_INTERVAL}", print_func=logging.debug)
    time.sleep(REFRESH_INTERVAL)
    guest_info = CryptoDeviceInfoBuilder.get(session)
    logging.info(f"Guest crypto devices: {guest_info}")

    domains = [str(x) for x in guest_info.domains]
    for device in devices:
        if str(device) not in domains:
            raise TestFail(f"Failed to find device: {device}")
    if len(devices) != len(guest_info.domains):
        raise TestFail(f"More devices present in guest than expected")
    output = session.cmd(f"lszcrypt -d", print_func=logging.debug)
    # count the 'B' entries in the matrix but ignore the legend footer
    output = re.sub(r"B:.*$", "", output)
    if len(re.findall(r"B", output)) != len(set([x.domain for x in devices])):
        raise TestFail(
            f"Not all devices are recognized as both usage "
            f"and control domains:\\n"
            f"devices: {devices}\\n"
            f"output: {output}"
        )


def run(test, params, env):
    """
    Tests dynamic update of vfio-ap passthrough matrix on s390x

    1. Check host crypto device availability
    2. Assign crypto devices to vfio_ap
    3. Define and start initial node device
    4. VM is already started (start_vm = yes)
    5. Attach host device to VM
    6. Update host matrix configuration
    7. Create updated node device XML
    8. Update running device via nodedev_update
    9. Verify updated matrix in guest
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    cards_before = int(params.get("cards_before", 0))
    cards_after = int(params.get("cards_after", 0))
    domains_before = int(params.get("domains_before", 0))
    domains_after = int(params.get("domains_after", 0))

    flagstr = params.get("flagstr")

    vmxml_backup = VMXML.new_from_inactive_dumpxml(vm_name)
    cleanup_actions.append(lambda: vmxml_backup.sync())

    global add_condition
    add_condition = params.get("add_condition", "")
    global device_uuid
    global device_uuid_2
    device_uuid = str(uuid4())
    device_uuid_2 = str(uuid4())
    global info
    info = CryptoDeviceInfoBuilder.get()
    session = None
    cmd = f"chzdev -t ap apmask=0-255 aqmask=0-255"
    cleanup_actions.append(lambda: cmd_status_output(cmd, shell=True))

    try:
        session = vm.wait_for_login()
        cleanup_actions.append(lambda: session.close())

        check_host_requirements(info, params)

        devices = get_devices(info, cards_before, domains_before)

        assign_crypto_devices_to_vfio(devices)

        first_xml = define_and_start_nodedev(devices)

        hostdev_xml = attach_hostdev_to_vm(vm_name)

        devices = get_devices(info, cards_after, domains_after)

        update_crypto_device_assignment(devices)

        if update_nodedev_xml(first_xml, flagstr, devices, test):
            return

        verify_matrix_in_guest(session, vm, first_xml.name, hostdev_xml, devices)

    finally:
        vm.destroy()
        cleanup_actions.reverse()
        for action in cleanup_actions:
            try:
                action()
            except:
                logging.debug(f"Failed to execute action {action}")
