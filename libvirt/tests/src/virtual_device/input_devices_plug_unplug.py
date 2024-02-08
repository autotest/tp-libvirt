#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#   Author: Dan Zheng <dzheng@redhat.com>
#

from virttest.libvirt_xml.devices.input import Input
from virttest.libvirt_xml.vm_xml import VMXML
from virttest import virsh


def prepare_vm_xml(vm_xml, device_type, test):
    """
    Remove specified devices from the vm xml for the test

    :param vm_xml: vm xml
    :param device_type: str, device type
    :param test: test object
    """
    vm_xml.remove_all_device_by_type(device_type)
    vm_xml.sync()
    test.log.debug("The VM xml after preparation:"
                   "\n%s", VMXML.new_from_dumpxml(vm_xml.vm_name))


def check_dumpxml(vm_name, expect_device_types, expect_bus, expect_exist, test):
    """
    Check whether the specified devices are(or aren't) shown in the guest xml

    :param vm_name: str, vm name
    :param expect_device_types: list, device types, like ['keyboard', 'mouse']
    :param expect_bus: str, device bus, like 'virtio'
    :param expect_exist: boolean, True for existence, False for not
    :param test: test object
    """
    current_vmxml = VMXML.new_from_dumpxml(vm_name)
    input_devices = current_vmxml.get_devices(device_type="input")
    test.log.debug("Current vm xml:\n%s", current_vmxml)
    for device_type in expect_device_types:
        found = False
        for dev in input_devices:
            if dev.type_name == device_type and dev.input_bus == expect_bus:
                found = True
                test.log.debug("Found the expected %s device "
                               "with %s bus", device_type, expect_bus)
                break
        if found == expect_exist:
            test.log.debug("Verify guest xml for the %s device "
                           "with %s bus - PASS", device_type, expect_bus)

        else:
            test.fail("Expect the %s device with %s bus %s "
                      "exist." % (device_type,
                                  expect_bus,
                                  'not' if not expect_exist else ''))


def run(test, params, env):
    """
    Test the input virtual devices

    1. Start a guest
    2. Test hotplug and hotunplug different input devices
    3. check the result
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    bus_type = params.get("bus_type")
    device_types = eval(params.get("device_types"))
    virsh_dargs = {'ignore_status': False, 'debug': True}
    device_list = []

    if vm.is_alive():
        vm.destroy()

    try:
        test.log.info("STEP: Clean input devices from VM xml.")
        prepare_vm_xml(vm_xml, 'input', test)
        test.log.info("STEP: Start VM")
        virsh.start(vm_name, **virsh_dargs)
        vm.wait_for_login().close()
        for input_type in device_types:
            input_dev = Input(type_name=input_type)
            input_dev.input_bus = bus_type
            device_list.append(input_dev)
            test.log.info("STEP: Hotplug device:%s", input_dev)
            virsh.attach_device(vm_name, input_dev.xml, **virsh_dargs)
        check_dumpxml(vm_name, device_types, bus_type, True, test)
        for input_dev in device_list:
            test.log.info("STEP: Hotunplug device:%s", input_dev)
            virsh.detach_device(vm_name, input_dev.xml,
                                wait_for_event=True, event_timeout=10,
                                **virsh_dargs)
        check_dumpxml(vm_name, device_types, bus_type, False, test)
    finally:
        if vm.is_alive():
            virsh.destroy(vm_name)
        vm_xml_backup.sync()
