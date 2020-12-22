import logging
import re
import glob
import platform

from virttest.libvirt_xml.devices.input import Input
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import virsh

from virttest import libvirt_version


def run(test, params, env):
    """
    Test the input virtual devices

    1. prepare a guest with different input devices
    2. check whether the guest can be started
    3. check the qemu cmd line
    """
    def check_dumpxml():
        """
        Check whether the added devices are shown in the guest xml
        """
        pattern = "<input bus=\"%s\" type=\"%s\">" % (bus_type, input_type)
        if with_packed:
            pattern = "<driver packed=\"%s\"" % (driver_packed)
        logging.debug('Searching for %s in vm xml', pattern)
        xml_after_adding_device = VMXML.new_from_dumpxml(vm_name)
        logging.debug('xml_after_adding_device:\n%s', xml_after_adding_device)
        if pattern not in str(xml_after_adding_device):
            test.fail("Can not find the %s input device xml "
                      "in the guest xml file." % input_type)

    def check_qemu_cmd_line():
        """
        Check whether the added devices are shown in the qemu cmd line
        """
        # if the tested input device is a keyboard or mouse with ps2 bus,
        # there is no keyboard or mouse in qemu cmd line
        if bus_type == "ps2" and input_type in ["keyboard", "mouse"]:
            return
        with open('/proc/%s/cmdline' % vm.get_pid(), 'r') as cmdline_file:
            cmdline = cmdline_file.read()
        if bus_type == "usb" and input_type == "keyboard":
            pattern = r"-device.%s-kbd" % bus_type
        elif input_type == "passthrough":
            pattern = r"-device.%s-input-host-pci" % bus_type
        else:
            pattern = r"-device.%s-%s" % (bus_type, input_type)
        if not re.search(pattern, cmdline):
            test.fail("Can not find the %s input device "
                      "in qemu cmd line." % input_type)
        if with_packed:
            pattern = r"packed=%s" % driver_packed
            if not re.search(pattern, cmdline):
                test.fail("Can not find the packed driver "
                          "in qemu cmd line")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    machine_type = params.get('machine_type', '')
    status_error = params.get("status_error", "no") == "yes"
    with_packed = params.get("with_packed", "no") == "yes"
    driver_packed = params.get("driver_packed", "on")
    bus_type = params.get("bus_type")
    input_type = params.get("input_type")

    check_preconditions(bus_type, input_type, with_packed, test)

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    if vm.is_alive():
        vm.destroy()

    try:
        # ps2 keyboard and ps2 mouse are default, no need to re-add the xml,
        # unless it's machine_type is pseries
        if not (bus_type == "ps2" and input_type in ["keyboard", "mouse"]
                and machine_type != 'pseries'):
            vm_xml.remove_all_device_by_type('input')
            input_dev = Input(type_name=input_type)
            input_dev.input_bus = bus_type
            if input_type == "passthrough":
                kbd_dev_name = glob.glob('/dev/input/by-path/*kbd')
                if not kbd_dev_name:
                    test.cancel("There is no keyboard device on this host.")
                logging.debug("keyboard %s is going to be passthrough "
                              "to the host.", kbd_dev_name[0])
                input_dev.source_evdev = kbd_dev_name[0]
            if with_packed:
                input_dev.driver_packed = driver_packed
            vm_xml.add_device(input_dev)
            try:
                vm_xml.sync()
            except Exception as error:
                if not status_error:
                    test.fail("Failed to define the guest after adding the %s input "
                              "device xml. Details: %s " % (input_type, error))
                logging.debug("This is the expected failing in negative cases.")
                return

        res = virsh.start(vm_name)
        if res.exit_status:
            if not status_error:
                test.fail("Failed to start vm after adding the %s input "
                          "device xml. Details: %s " % (input_type, res.stderr))
            logging.debug("This is the expected failure in negative cases.")
            return
        if status_error:
            test.fail("Expected fail in negative cases but vm started successfully.")
            return

        logging.debug("VM started successfully in postive cases.")
        check_dumpxml()
        check_qemu_cmd_line()
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        vm_xml_backup.sync()


def check_preconditions(bus_type, input_type, with_packed, test):
    if input_type == "tablet":
        if not libvirt_version.version_compare(1, 2, 2):
            test.cancel("tablet input type is not supported "
                        "on the current version.")
    if input_type == "passthrough" or bus_type == "virtio":
        if not libvirt_version.version_compare(1, 3, 0):
            test.cancel("passthrough input type or virtio bus type "
                        "is not supported on current version.")
    if bus_type in ["ps2", "usb"] and platform.machine() == 's390x':
        test.cancel("bus types ps2, usb not supported on s390x")
    if with_packed and not libvirt_version.version_compare(6, 3, 0):
        test.cancel("The virtio packed attribute is not supported in"
                    " current libvirt version.")
