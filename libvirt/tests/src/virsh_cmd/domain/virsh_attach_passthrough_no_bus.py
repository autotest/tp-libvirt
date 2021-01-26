import logging
from virttest.libvirt_xml.devices.input import Input
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import virsh
from virttest import libvirt_version


def run(test, params, env):
    """
    Test the virtio bus autommated assignement for passthrough devices

    1. prepare a passthrough device xml without bus defined
    2. start the guest and check if the device can be attached
    3. check if the new device is properly listed in guest xml
    """
    if not libvirt_version.version_compare(6, 3, 0):
        test.cancel('The feature of automatic assignment of virtio bus for '
                    'passthrough devices is supported since version 6.3.0')
    vm_name = params.get("main_vm", "avocado-vt-vm1")

    # Create a new passthrough device without bus assigned
    input_dev = Input(type_name="passthrough")
    input_dev.source_evdev = "/dev/input/event1"
    xml = input_dev.get_xml()
    logging.debug('Attached device xml:\n{}'.format(input_dev.xmltreefile))
    logging.debug('New Passthrough device XML is available at:{}'.format(xml))
    # Start the VM
    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    if vm.is_alive():
        vm.destroy()
    vm_xml.remove_all_device_by_type('input')

    try:
        vm.start()
        vm.wait_for_login().close()
        # Attach new device and check for result
        cmd_result = virsh.attach_device(vm_name, input_dev.get_xml(), debug=True)
        if cmd_result.exit_status != 0:
            test.error(cmd_result.stderr_text)
        # Get the VM XML and check for a new device
        vm_xml = VMXML.new_from_dumpxml(vm_name)
        device_list = vm_xml.get_devices()
        for device in device_list:
            if device['device_tag'] == 'input':
                device_xml = device['xml']
                # Create a new instance of Input device and fill with input
                # device found
                input_device = Input(type_name="passthrough")
                input_device.set_xml(device_xml)
                if input_device.type_name == "passthrough":
                    with open(device_xml, 'r') as device_xml_file:
                        for line in device_xml_file:
                            logging.debug(line.rstrip())
                    if not input_device.input_bus == "virtio":
                        test.fail("The newly attached passthrough device has no"
                                  " added virtio as a bus by default.")
                    else:
                        logging.debug("Newly added passthrough device has a "
                                      "virtio automatically assigned as a bus.")
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        vm_xml_backup.sync()
