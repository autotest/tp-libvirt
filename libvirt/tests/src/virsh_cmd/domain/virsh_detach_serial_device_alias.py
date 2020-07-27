import os
import logging
import random
import string
import platform

from virttest import virsh
from virttest import libvirt_version
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices import librarian


def run(test, params, env):
    """
    Test for basic detach serial/console device by alias function.

    1) Define the VM with specified serial type device.
    2) Start the guest and check if start result meets expectation
    3) Hot unplug serial device and check if meets expectation
    4) Cold unplug serial device and check if meets expectation
    5) Shutdown the VM and clean up environment
    """

    def set_targets(serial):
        """
        Set a serial device target attributes.

        :param serial: one serial target
        """
        machine = platform.machine()
        if "ppc" in machine:
            serial.target_model = 'spapr-vty'
            serial.target_type = 'spapr-vio-serial'
        elif "aarch" in machine:
            serial.target_model = 'pl011'
            serial.target_type = 'system-serial'
        else:
            serial.target_model = target_type
            serial.target_type = target_type

    def prepare_serial_device():
        """
        Prepare a serial device XML
        """
        local_serial_type = serial_type
        serial = librarian.get('serial')(local_serial_type)
        serial.target_port = "0"
        serial.alias = {'name': alias_name}

        set_targets(serial)

        sources = []
        logging.debug(sources_str)
        for source_str in sources_str.split():
            source_dict = {}
            for att in source_str.split(','):
                key, val = att.split(':')
                source_dict[key] = val
            sources.append(source_dict)
        serial.sources = sources
        return serial

    def check_vm_xml(existed=True, inactive=False):
        """
        Check VM xml file to validate whether serial and console elements exists.

        :param existed: Default is True indicate whether element exist or not
        :param inactive: indicate VM xml is from active or inactive VM.
        """
        # Get current serial and console XML
        current_xml = VMXML.new_from_dumpxml(vm_name)
        if inactive:
            current_xml = VMXML.new_from_inactive_dumpxml(vm_name)
        serial_elem = current_xml.xmltreefile.find('devices/serial')
        console_elem = current_xml.xmltreefile.find('devices/console')
        if existed:
            if serial_elem is None:
                test.fail("Expect generate serial"
                          "but found none.")
            if target_type != 'pci-serial' and console_elem is None:
                test.fail("Expect generate console automatically, "
                          "but found none.")

    def cleanup():
        """
        Clean up test environment
        """
        if serial_type == 'file':
            if os.path.exists('/var/log/libvirt/virt-test'):
                os.remove('/var/log/libvirt/virt-test')

    serial_type = params.get('serial_type', 'pty')
    target_type = params.get('target_type', 'isa-serial')
    sources_str = params.get('serial_sources', '')
    hot_plug_support = "yes" == params.get('hot_plugging_support')

    # Customize alias name string.
    chars = string.ascii_letters + string.digits + '-_'
    alias_name = 'ua-' + ''.join(random.choice(chars) for _ in list(range(64)))

    vm_name = params.get('main_vm', 'alias-vm-tests-vm1')
    vm = env.get_vm(vm_name)

    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        if not libvirt_version.version_compare(4, 5, 0):
            test.cancel("virsh detach-device-alias is supported until libvirt 4.5.0 version")
        vm_xml.remove_all_device_by_type('serial')
        vm_xml.remove_all_device_by_type('console')

        serial_dev = prepare_serial_device()
        logging.debug('Serial device:\n%s', serial_dev)
        vm_xml.add_device(serial_dev)
        vm_xml.sync()

        vm.start()
        check_vm_xml()

        # Hot detach device by its alias name.
        # If hotplug not supported, serial should be still there.
        res = virsh.detach_device_alias(vm_name, alias_name, "--live")
        libvirt.check_exit_status(res, not hot_plug_support)
        check_vm_xml(existed=not hot_plug_support)

        # Cold detach device by its alias name.
        # After detach, serial should not be there.
        res = virsh.detach_device_alias(vm_name, alias_name, "--config")
        libvirt.check_exit_status(res)
        check_vm_xml(existed=False, inactive=True)
        debug_xml = VMXML.new_from_inactive_dumpxml(vm_name)
        logging.debug("After VM cold detached:%s", debug_xml)
        vm.destroy()
    finally:
        cleanup()
        vm_xml_backup.sync()
