from aexpect import ShellError

from virttest.virt_vm import VMStartError
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.xcepts import LibvirtXMLError
from virttest.remote import LoginError


def run(test, params, env):
    """
    Test for adding controller for usb.
    """
    # get the params from params
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)

    index = params.get("index", "1")
    index_conflict = "yes" == params.get("index_conflict", "no")
    model = params.get("model", "nec-xhci")

    status_error = "yes" == params.get("status_error", "no")

    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    controllers = vm_xml.get_devices(device_type="controller")
    devices = vm_xml.get_devices()
    for dev in controllers:
        if dev.type == "usb":
            devices.remove(dev)
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
