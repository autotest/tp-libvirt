import os
import platform

from virttest import libvirt_version
from virttest import utils_net
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.controller import Controller  # pylint: disable=W0611
from virttest.libvirt_xml.devices.disk import Disk  # pylint: disable=W0611
from virttest.libvirt_xml.devices.filesystem import Filesystem  # pylint: disable=W0611
from virttest.libvirt_xml.devices.interface import Interface  # pylint: disable=W0611
from virttest.libvirt_xml.devices.input import Input  # pylint: disable=W0611
from virttest.libvirt_xml.devices.memballoon import Memballoon  # pylint: disable=W0611
from virttest.libvirt_xml.devices.rng import Rng  # pylint: disable=W0611
from virttest.libvirt_xml.devices.video import Video  # pylint: disable=W0611
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Start guest with virtio page_per_vq attribute - various virtio devices
    1) Prepare a guest with virtio page_per_vq attribute in different virtio devices.
    2) Start the guest.
    3) Login the vm and check network.
    """

    def prepare_test(vmxml):
        """
        Prepare the guest with different virtio devices to test

        :params vmxml: the vm xml
        """
        if not ((device_type == "disk" and hotplug) or device_type == "input"):
            vmxml.remove_all_device_by_type(device_type)
        # For now, arm can not create USB controller automatically.
        if device_type == "controller" and platform.machine() == 'aarch64':
            usb_controller = Controller("controller")
            usb_controller.type = "usb"
            usb_controller.model = 'qemu-xhci'
            vmxml.add_device(usb_controller)
        vmxml.sync()
        # Need to use shared memory for filesystem device
        if device_type == "filesystem":
            vm_xml.VMXML.set_memoryBacking_tag(vmxml.vm_name, access_mode="shared",
                                               hpgs=False)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # Prepare device xml by using device function, for example, Disk().
        device_obj = device_type.capitalize()
        if device_type == "disk" and hotplug:
            disk_image_path = os.path.join(os.path.dirname(disk_image), "base.qcow2")
            libvirt.create_local_disk(
                "file", path=disk_image_path,
                size="1G", disk_format="qcow2")
            device_dict['source']['attrs']['file'] = disk_image_path
        if device_type == "input":
            device_xml = eval(device_obj)(input_type)
        else:
            device_xml = eval(device_obj)()
        device_xml.setup_attrs(**device_dict)
        return device_xml, vmxml

    def run_test(device_xml, vmxml):
        """
        Start a guest and check the network.

        :params device_xml: the device xml prepared in prepare_test
        "params vmxml: the vm xml after prepare_test()
        """
        if not hotplug:
            vmxml.add_device(device_xml)
            vmxml.sync()
            test.log.info("TEST_STEP1: start guest")
            start_guest()
        else:
            test.log.info("TEST_STEP1: hotplug %s device", device_type)
            start_guest()
            virsh.attach_device(vm_name, device_xml.xml, ignore_status=False, debug=True)
        vm.cleanup_serial_console()
        vm.create_serial_console()
        vm.wait_for_serial_login().close()

        test.log.info("TEST_STEP2: check the attribute in %s xml", device_type)
        check_attribute()
        if hotplug:
            virsh.detach_device(vm_name, device_xml.xml, ignore_status=False, debug=True)
        test.log.info("TEST_STEP3: check the network by ping")
        utils_net.ping(dest=ping_outside, count='3', timeout=10, session=vm.session, force_ipv4=True)

    def teardown_test():
        """
        Clean up the test environment.
        """
        bkxml.sync()
        if hotplug and os.path.exists(disk_image):
            os.remove(disk_image)

    def check_attribute():
        """
        Check the page_per_vq attribute after starting the guest
        """
        af_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        test.log.info("The current dumpxml is %s", af_vmxml)
        # Keyboard and mouse input will be default in guest. So identify input device.
        if device_type == "input":
            input_element = [x for x in af_vmxml.get_devices(device_type)
                             if x['input_type'] == input_type
                             and 'driver' in x.fetch_attrs()]
            if not input_element:
                test.fail("The expected input device was not found.")
            dev_xml = input_element[0]
        # Guest has many controllers, so also need to identify it.
        elif device_type == "controller":
            dev_xml = af_vmxml.get_devices(device_type)
        elif device_type == "disk" and hotplug:
            dev_xml = af_vmxml.get_devices(device_type)[1]
        else:
            dev_xml = af_vmxml.get_devices(device_type)[0]
        # Select the virtio-scsi/virtio-serial controller from all controllers
        if device_type == "controller":
            for controller in dev_xml:
                if controller.type == controller_type:
                    controller_attrs = controller.fetch_attrs()
                    test.log.debug("controller attrs: %s", controller_attrs)
                    cur_dict = controller_attrs["driver"]
        else:
            dev_attrs = dev_xml.fetch_attrs()
            test.log.debug("dev attrs: %s", dev_attrs)
            cur_dict = dev_attrs["driver"]
        pre_dict = driver_dict["driver"]
        for key, value in pre_dict.items():
            if cur_dict.get(key) != value:
                test.fail("Driver XML compare fails. It should be '%s', but "
                          "got '%s'" % (pre_dict, cur_dict))
            else:
                test.log.debug("Driver XML compare successfully. The '%s' matches"
                               " the '%s'", (pre_dict, cur_dict))

    def start_guest():
        """
        Start or reboot the guest
        """
        test.log.info("Start the guest")
        if not vm.is_alive():
            vm.start()

    vm_name = params.get("main_vm")
    device_type = params.get("device_type")
    driver_dict = eval(params.get("driver_dict", "{}"))
    ping_outside = params.get("ping_outside")
    hotplug = params.get("hotplug", "no") == "yes"
    device_dict = eval(params.get("device_dict", "{}"))
    disk_image = params.get("disk_image", "")
    input_type = params.get("input_type")
    controller_type = params.get("controller_type")
    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    bkxml = vmxml.copy()

    try:
        device_xml, vmxml = prepare_test(vmxml)
        run_test(device_xml, vmxml)
    finally:
        teardown_test()
