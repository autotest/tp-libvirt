from aexpect import ShellError

from virttest.virt_vm import VMStartError
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.xcepts import LibvirtXMLError
from virttest.remote import LoginError


def validate_multiple_controller(test, vm_name):
    """
    Validate multiple controller.

    :param test: test itself
    :param vm_name: vm name
    """
    expect_index_list = ['0', '0', '0', '0', '1', '1', '1', '1', '2', '2', '2', '2']
    actual_index_list = []
    vm_xml = VMXML.new_from_dumpxml(vm_name)
    controllers = vm_xml.get_devices(device_type="controller")
    devices = vm_xml.get_devices()
    for dev in controllers:
        if dev.type == "usb":
            actual_index_list.append(dev.index)
    for actual_index, expect_index in zip(actual_index_list, expect_index_list):
        if actual_index != expect_index:
            test.fail("usb controller are not organizated by index group")


def run(test, params, env):
    """
    Test for adding controller for usb.
    """
    # get the params from params
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    index = params.get("index", "1")
    index_conflict = "yes" == params.get("index_conflict", "no")
    index_multiple = index == "multiple"
    model = params.get("model", "nec-xhci")

    status_error = "yes" == params.get("status_error", "no")

    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    controllers = vm_xml.get_devices(device_type="controller")
    devices = vm_xml.get_devices()
    for dev in controllers:
        if dev.type == "usb":
            devices.remove(dev)
    # Test add multiple controllers in disorder.
    if index_multiple:
        # Remove input devices dependent on usb controller.
        inputs = vm_xml.get_devices(device_type="input")
        for input_device in inputs:
            if input_device.type_name == "tablet":
                vm_xml.del_device(input_device)
        model = 'ich9-hci'
        # Initialize one usb controller list.
        controller_list = [('0', 'ich9-uhci2'),
                           ('2', 'ich9-uhci2'),
                           ('0', 'ich9-uhci3'),
                           ('1', 'ich9-uhci1'),
                           ('2', 'ich9-uhci3'),
                           ('0', 'ich9-ehci1'),
                           ('2', 'ich9-ehci1'),
                           ('1', 'ich9-uhci3'),
                           ('1', 'ich9-uhci2'),
                           ('1', 'ich9-ehci1'),
                           ('2', 'ich9-uhci1'),
                           ('0', 'ich9-uhci1')]
        # Add multiple usb controllers in random order.
        for usb_tuple in controller_list:
            controller = Controller("controller")
            controller.type = "usb"
            controller.index = usb_tuple[0]
            controller.model = usb_tuple[1]
            devices.append(controller)
    else:
        controller = Controller("controller")
        controller.type = "usb"
        controller.index = index
        controller.model = model
        devices.append(controller)
    if index_conflict:
        controller_1 = Controller("controller")
        controller_1.type = "usb"
        controller_1.index = index
        devices.append(controller)

    vm_xml.set_devices(devices)
    try:
        try:
            vm_xml.sync()
            vm.start()
            # Validate multiple usb controllers result, disorder controllers will be organized by index group.
            if index_multiple:
                validate_multiple_controller(test, vm_name)
            if status_error:
                test.fail("Add controller successfully in negative case.")
            else:
                try:
                    session = vm.wait_for_login()
                except (LoginError, ShellError) as e:
                    error_msg = "Test failed in positive case.\n error: %s\n" % e
                    test.fail(error_msg)
                cmd = "dmesg -c | grep %s" % model.split('-')[-1]
                stat_dmesg = session.cmd_status(cmd)
                if stat_dmesg != 0:
                    test.cancel("Fail to run dmesg in guest")
                session.close()
        except (LibvirtXMLError, VMStartError) as e:
            if not status_error:
                test.fail("Add controller failed. Detail: %s" % e)
    finally:
        vm_xml_backup.sync()
