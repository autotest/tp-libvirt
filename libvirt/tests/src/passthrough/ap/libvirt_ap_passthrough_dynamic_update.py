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
from virttest.utils_zcrypt import CryptoDeviceInfoBuilder, load_vfio_ap, unload_vfio_ap

from provider.vfio import ap, get_nodedev_xml

logging = log.getLogger("avocado." + __name__)


class EarlyTerminationException(Exception):
    """Exception raised when test execution should be terminated early."""

    pass


class Steps:
    """
    Class to handle the steps of the libvirt AP passthrough dynamic update test.
    """

    REFRESH_INTERVAL = 5

    def __init__(self, env, test, params):
        """
        Initialize the Steps class with test parameters.

        :param env: Test environment
        :param test: Test instance
        :param params: Test parameters
        """
        self.env = env
        self.test = test
        self.params = params

        # VM-related parameters
        self.vm_name = params.get("main_vm")
        self.vm = env.get_vm(self.vm_name)

        # Test configuration parameters
        self.cards_before = int(params.get("cards_before", 0))
        self.cards_after = int(params.get("cards_after", 0))
        self.domains_before = int(params.get("domains_before", 0))
        self.domains_after = int(params.get("domains_after", 0))
        self.flagstr = params.get("flagstr")
        self.add_condition = params.get("add_condition", "")

        # Test state variables
        self.vmxml_backup = None
        self.session = None
        self.cleanup_actions = []
        self.device_uuid = str(uuid4())
        self.device_uuid_2 = str(uuid4())

        # Test execution state - results from methods
        self.devices = None
        self.first_xml = None
        self.hostdev_xml = None

        # Host crypto device info - initialized once
        self.info = CryptoDeviceInfoBuilder.get()

        logging.info(
            f"Initialized Steps for VM {self.vm_name}: "
            f"cards {self.cards_before}->{self.cards_after}, "
            f"domains {self.domains_before}->{self.domains_after}, "
            f"additional condition: {self.add_condition}"
        )
        logging.info(f"Host crypto devices: {self.info}")

        self.check_host_requirements()

        load_vfio_ap()
        self.cleanup_actions.append(lambda: unload_vfio_ap())

        self.session = self.vm.wait_for_login()
        self.cleanup_actions.append(lambda: self.session.close())

    def update_device_info(self, cards, domains):
        """
        Update self.devices with the list of devices in the cards X domains matrix.

        :param cards: Number of cards to include in the matrix
        :param domains: Number of domains to include in the matrix
        """
        logging.debug("All devices: %s", [str(x) for x in self.info.domains])
        _cards = list(set([x.card for x in self.info.domains]))[:cards]
        _domains = list(set([x.domain for x in self.info.domains]))[:domains]
        self.devices = [
            x for x in self.info.domains if x.card in _cards and x.domain in _domains
        ]
        logging.debug("Filtered devices: %s", [str(x) for x in self.devices])

    def check_host_requirements(self):
        """
        Check if host has sufficient crypto cards and domains available.
        Validates that the host system meets the minimum requirements for the test.

        :raises TestError: If requirements are not met (insufficient devices, wrong hwtype, etc.)
        """
        min_cards = max(self.cards_before, self.cards_after)
        min_domains = max(self.domains_before, self.domains_after)

        if not self.info.entries:
            raise TestError("No crypto devices found on host")

        # Check if we have enough cards and domains
        available_cards = set()
        available_domains = set()

        for entry in self.info.entries:
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

        if self.info.domains and int(self.info.domains[0].hwtype) < 10:
            raise TestError("vfio-ap requires at least HWTYPE 10")

        logging.info("Host requirements check passed")

    def update_crypto_device_assignment(self):
        """
        Update crypto device assignment for the second phase of the test.
        """
        self.assign_crypto_devices_to_vfio(num_calls=2)

    def assign_crypto_devices_to_vfio(self, num_calls=1):
        """
        Assign host crypto devices to vfio_ap using chzdev -t ap command.

        :param num_calls: Call number (1 for initial assignment, 2 for update phase)
        :raises TestError: If device assignment fails
        """
        if num_calls == 2 and "do_not_update_host_matrix" == self.add_condition:
            return

        for device in self.devices:
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
            f"Successfully assigned {len(self.devices)} crypto devices to vfio_ap"
        )

    def attach_hostdev_to_vm(self):
        """
        Define host device XML and attach it to the running VM.
        Sets self.hostdev_xml with the result.
        """
        self.hostdev_xml = ap.attach_hostdev(self.vm_name, self.device_uuid)
        logging.info(f"Successfully attached hostdev to VM {self.vm_name}")

    def update_nodedev_xml(self):
        """
        Update the already running device with virsh.nodedev_update.
        Raises EarlyTerminationException if test should terminate early.
        """
        updated_xml = get_nodedev_xml(
            "vfio_ap-passthrough",
            "ap_matrix",
            self.device_uuid,
            [".".join(x.id) for x in self.devices],
            name=self.first_xml.name,
        )

        if "undefine_device_before_update" == self.add_condition:
            virsh.nodedev_undefine(self.first_xml.name, debug=True, ignore_status=False)
        if "create_second_device_with_after_values" == self.add_condition:
            second_xml = get_nodedev_xml(
                "vfio_ap-passthrough",
                "ap_matrix",
                self.device_uuid_2,
                [".".join(x.id) for x in self.devices],
            )
            result = virsh.nodedev_create(
                second_xml.xml, debug=True, ignore_status=False
            )
            pattern = r"Node device ([a-z0-9_']+) created"
            name = re.search(pattern, result.stdout_text)[1].strip("'")
            self.cleanup_actions.append(
                lambda: virsh.nodedev_destroy(name, debug=True, ignore_status=False)
            )

        result = virsh.nodedev_update(
            self.first_xml.name,
            updated_xml.xml,
            options=self.flagstr,
            debug=True,
            ignore_status=True,
        )

        if self.add_condition in [
            "create_second_device_with_after_values",
            "do_not_update_host_matrix",
            "undefine_device_before_update",
        ]:
            if result.exit_status:
                raise EarlyTerminationException(
                    "Test execution terminated early due to specific condition"
                )
            else:
                raise TestFail(
                    f"For error condition '{self.add_condition}'"
                    " the node device update should fail"
                    " but it didn't."
                )
        if result.exit_status:
            raise TestError(f"Failed to update running device: {result.stderr}")

    def define_and_start_nodedev(self):
        """
        Define and start a node device for vfio-ap passthrough.
        """
        nodedev_xml = get_nodedev_xml(
            "vfio_ap-passthrough",
            "ap_matrix",
            self.device_uuid,
            [".".join(x.id) for x in self.devices],
        )
        result = virsh.nodedev_define(nodedev_xml.xml, debug=True, ignore_status=True)
        if result.exit_status:
            raise TestError(f"Failed to define device: {result.stderr}")
        pattern = r"Node device ([a-z0-9_']+) defined"
        name = re.search(pattern, result.stdout_text)[1].strip("'")
        self.cleanup_actions.append(
            lambda: virsh.nodedev_undefine(name, debug=True, ignore_status=False)
        )

        result = virsh.nodedev_start(name, debug=True, ignore_status=True)
        if result.exit_status:
            raise TestError(f"Failed to start device: {result.stderr}")
        self.cleanup_actions.append(
            lambda: virsh.nodedev_destroy(name, debug=True, ignore_status=False)
        )
        nodedev_xml.name = name
        nodedev_xml.xmltreefile.write()
        self.first_xml = nodedev_xml

    def verify_matrix_in_guest(self):
        """
        Confirm that the updated matrix is correct inside the guest.
        """
        if "restart_all_before_guest_verification" == self.add_condition:
            self.session.close()
            virsh.destroy(self.vm_name, debug=True, ignore_status=False)
            virsh.nodedev_destroy(self.first_xml.name, debug=True, ignore_status=False)
            virsh.nodedev_start(self.first_xml.name, debug=True, ignore_status=False)
            self.vm.start()
            virsh.attach_device(
                self.vm.name, self.hostdev_xml.xml, debug=True, ignore_status=False
            )
            self.session = self.vm.wait_for_login()

        self.session.cmd(
            f"chzcrypt -c {Steps.REFRESH_INTERVAL}", print_func=logging.debug
        )
        time.sleep(Steps.REFRESH_INTERVAL)
        guest_info = CryptoDeviceInfoBuilder.get(self.session)
        logging.info(f"Guest crypto devices: {guest_info}")

        domains = [str(x) for x in guest_info.domains]
        for device in self.devices:
            if str(device) not in domains:
                raise TestFail(f"Failed to find device: {device}")
        if len(self.devices) != len(guest_info.domains):
            raise TestFail(f"More devices present in guest than expected")
        output = self.session.cmd(f"lszcrypt -d", print_func=logging.debug)
        # count the 'B' entries in the matrix but ignore the legend footer
        output = re.sub(r"B:.*$", "", output)
        if len(re.findall(r"B", output)) != len(set([x.domain for x in self.devices])):
            raise TestFail(
                f"Not all devices are recognized as both usage "
                f"and control domains:\\n"
                f"devices: {self.devices}\\n"
                f"output: {output}"
            )

    def setup_vm_backup(self):
        """
        Create VM XML backup for cleanup purposes.
        Saves the inactive VM XML configuration and adds restoration to cleanup actions.
        """
        self.vmxml_backup = VMXML.new_from_inactive_dumpxml(self.vm_name)
        self.cleanup_actions.append(lambda: self.vmxml_backup.sync())
        logging.info(f"Created VM XML backup for {self.vm_name}")

    def setup_cleanup(self):
        """
        Setup cleanup actions for restoring host crypto device configuration.
        Adds command to reset all crypto device masks to default state.
        """
        cmd = "chzdev -t ap apmask=0-255 aqmask=0-255"
        self.cleanup_actions.append(lambda: cmd_status_output(cmd, shell=True))

    def cleanup(self):
        """
        Cleanup resources and restore original state.
        Destroys VM, executes all cleanup actions in reverse order, and handles errors gracefully.
        """
        try:
            self.vm.destroy()
        except:
            logging.debug("Failed to destroy VM during cleanup")

        self.cleanup_actions.reverse()
        for action in self.cleanup_actions:
            try:
                action()
            except:
                logging.debug(f"Failed to execute action {action}")


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
    steps = Steps(env, test, params)
    steps.setup_vm_backup()
    steps.setup_cleanup()

    try:
        # Get initial devices and set up
        steps.update_device_info(steps.cards_before, steps.domains_before)
        steps.assign_crypto_devices_to_vfio()
        steps.define_and_start_nodedev()
        steps.attach_hostdev_to_vm()

        # Get updated devices and perform update
        steps.update_device_info(steps.cards_after, steps.domains_after)
        steps.update_crypto_device_assignment()
        steps.update_nodedev_xml()
        steps.verify_matrix_in_guest()

        logging.info("Dynamic AP passthrough update test completed successfully")

    except EarlyTerminationException:
        # Handle early termination gracefully
        logging.info("Test terminated early due to add_condition")
    finally:
        steps.cleanup()
