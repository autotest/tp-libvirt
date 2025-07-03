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
from avocado.core.exceptions import TestError, TestFail
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import virsh
from virttest.utils_misc import cmd_status_output
from virttest.utils_zcrypt import CryptoDeviceInfoBuilder

from provider.vfio.mdev_handlers import MdevHandler
from provider.vfio import ap, get_nodedev_xml

logging = log.getLogger("avocado." + __name__)


def check_host_requirements(test, params):
    """
    Check if host has sufficient crypto cards and domains available.
    Abort with test.error if requirements not met.
    """
    cards_before = int(params.get("cards_before", 1))
    cards_after = int(params.get("cards_after", 1))
    domains_before = int(params.get("domains_before", 1))
    domains_after = int(params.get("domains_after", 1))
    
    max_cards = max(cards_before, cards_after)
    max_domains = max(domains_before, domains_after)
    
    try:
        info = CryptoDeviceInfoBuilder.get()
        logging.info("Host crypto devices: %s", info)
        
        if not info.entries:
            test.error("No crypto devices found on host")
            
        # Check if we have enough cards and domains
        available_cards = set()
        available_domains = set()
        
        for entry in info.domains:
            if entry.card:
                available_cards.add(entry.card)
            if entry.domain:
                available_domains.add(entry.domain)
        
        if len(available_cards) < max_cards:
            test.error(f"Not enough crypto cards available. Required: {max_cards}, Available: {len(available_cards)}")
            
        if len(available_domains) < max_domains:
            test.error(f"Not enough crypto domains available. Required: {max_domains}, Available: {len(available_domains)}")
            
        # Check minimum hardware type
        if info.domains and int(info.domains[0].hwtype) < 10:
            test.error("vfio-ap requires at least HWTYPE 10")
            
        logging.info("Host requirements check passed")
        
    except Exception as e:
        test.error(f"Failed to check host requirements: {e}")


def assign_crypto_devices_to_vfio(cards_before, domains_before):
    """
    Assign host crypto devices to vfio_ap using chzdev -t ap command.
    """
    try:
        info = CryptoDeviceInfoBuilder.get()
        selected_devices = info.domains[:cards_before * domains_before]
        
        for device in selected_devices:
            # Convert hex to decimal for chzdev
            card_dec = int(device.card, 16)
            domain_dec = int(device.domain, 16)
            
            cmd = f"chzdev -t ap apmask=-{card_dec} aqmask=-{domain_dec}"
            err, out = cmd_status_output(cmd, shell=True)
            if err:
                raise TestError(f"Failed to assign crypto device {device.card}.{device.domain}: {out}")
                
        logging.info(f"Successfully assigned {len(selected_devices)} crypto devices to vfio_ap")
        
    except Exception as e:
        raise TestError(f"Failed to assign crypto devices to vfio_ap: {e}")


def create_initial_nodedev(handler):
    """
    Define and start a node device for the initial matrix.
    """
    try:
        # Create the node device using the handler
        nodedev_name = handler.create_nodedev(api="nodedev")
        handler.nodedev_name = nodedev_name
        logging.info(f"Created initial node device: {nodedev_name}")
        return nodedev_name
        
    except Exception as e:
        raise TestError(f"Failed to create initial node device: {e}")


def attach_hostdev_to_vm(vm_name, handler):
    """
    Define host device XML and attach it to the running VM.
    """
    try:
        # Use the AP module's attach_hostdev function
        ap.attach_hostdev(vm_name, handler.uuid)
        logging.info(f"Successfully attached hostdev to VM {vm_name}")
        
    except Exception as e:
        raise TestError(f"Failed to attach hostdev to VM: {e}")


def update_host_matrix(cards_after, domains_after):
    """
    Update the host matrix with chzdev -t ap for new device assignment.
    """
    try:
        info = CryptoDeviceInfoBuilder.get()
        # Select devices based on the new requirements
        selected_devices = info.domains[:cards_after * domains_after]
        
        for device in selected_devices:
            # Convert hex to decimal for chzdev
            card_dec = int(device.card, 16)
            domain_dec = int(device.domain, 16)
            
            cmd = f"chzdev -t ap apmask=-{card_dec} aqmask=-{domain_dec}"
            err, out = cmd_status_output(cmd, shell=True)
            if err:
                raise TestError(f"Failed to update host matrix for device {device.card}.{device.domain}: {out}")
                
        logging.info(f"Successfully updated host matrix with {len(selected_devices)} devices")
        
    except Exception as e:
        raise TestError(f"Failed to update host matrix: {e}")


def create_updated_nodedev_xml(handler, cards_after, domains_after):
    """
    Create node device XML that defines the new configuration.
    """
    try:
        info = CryptoDeviceInfoBuilder.get()
        # Select the devices for the new configuration
        selected_devices = info.domains[:cards_after * domains_after]
        
        # Create domains list in the expected format
        domains = []
        for device in selected_devices:
            domains.append(f"{device.card}.{device.domain}")
        
        # Create updated node device XML
        device_xml = get_nodedev_xml("vfio_ap-passthrough", "ap_matrix", handler.uuid, domains)
        handler.updated_xml = device_xml
        
        logging.info(f"Created updated node device XML with domains: {domains}")
        return device_xml
        
    except Exception as e:
        raise TestError(f"Failed to create updated node device XML: {e}")


def update_running_device(handler):
    """
    Update the already running device with virsh.nodedev_update.
    """
    try:
        # Update the running device using the updated XML
        result = virsh.nodedev_update(handler.nodedev_name, handler.updated_xml.xml, 
                                    flagstr="--live", debug=True, ignore_status=False)
        if result.exit_status:
            raise TestError(f"Failed to update running device: {result.stderr}")
            
        logging.info(f"Successfully updated running device {handler.nodedev_name}")
        
    except Exception as e:
        raise TestError(f"Failed to update running device: {e}")


def verify_matrix_in_guest(vm, cards_after, domains_after):
    """
    Confirm that the updated matrix is correct inside the guest.
    """
    try:
        session = vm.wait_for_login()
        
        # Get guest crypto device info
        guest_info = CryptoDeviceInfoBuilder.get(session)
        logging.info(f"Guest crypto devices: {guest_info}")
        
        if not guest_info.domains:
            raise TestFail("No crypto domains found in guest after update")
            
        # Verify we have the expected number of devices
        expected_devices = cards_after * domains_after
        actual_devices = len(guest_info.domains)
        
        if actual_devices != expected_devices:
            raise TestFail(f"Expected {expected_devices} crypto devices in guest, "
                          f"but found {actual_devices}")
        
        # Verify devices are working by checking their status
        for device in guest_info.domains:
            if device.status != "online":
                logging.warning(f"Device {device.card}.{device.domain} is not online: {device.status}")
        
        logging.info(f"Matrix verification passed: {actual_devices} devices found in guest")
        session.close()
        
    except Exception as e:
        raise TestFail(f"Failed to verify matrix in guest: {e}")


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
    
    # Get test parameters
    cards_before = int(params.get("cards_before", 1))
    cards_after = int(params.get("cards_after", 1))
    domains_before = int(params.get("domains_before", 1))
    domains_after = int(params.get("domains_after", 1))
    
    vmxml_backup = VMXML.new_from_inactive_dumpxml(vm_name)
    handler = None

    try:
        check_host_requirements(test, params)
        
        assign_crypto_devices_to_vfio(cards_before, domains_before)
        
        handler = MdevHandler.from_type("vfio_ap-passthrough")
        create_initial_nodedev(handler)
        
        if vm.is_dead():
            test.error("VM should be running but is not started")
        
        session = vm.wait_for_login()
        
        attach_hostdev_to_vm(vm_name, handler)
        
        update_host_matrix(cards_after, domains_after)
        
        create_updated_nodedev_xml(handler, cards_after, domains_after)
        
        update_running_device(handler)
        
        verify_matrix_in_guest(vm, cards_after, domains_after)
        
        logging.info("Dynamic AP passthrough update test completed successfully")

    finally:
        vmxml_backup.sync()
        if handler:
            handler.clean_up()
