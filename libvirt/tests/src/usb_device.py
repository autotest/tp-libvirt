import re
import logging

from avocado.utils import process

from virttest import virsh
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.devices.hub import Hub


def run(test, params, env):
    """
    Test libvirt usb feature based on the following matrix:
        the combination usage of machine type q35/i440fx, pci/pcie
    bus controller and usb controller

    bus controller on q35 machine:
        pcie-root,pcie-root-port,pcie-to-pci-bridge,pci-bridge
        pcie-root,pcie-root-port,pcie-switch-upstream-port, pcie-switch-downstream-port
        pcie-root,dmi-to-pci-bridge,pci-bridge
    bus controller on i440fx machine:
        pci-root,pci-bridge

    usb30_controller:
        nec-xhci
        qemu-xhci
    usb20_controller:
        ich9-ehci1,ich9-uhci1,ich9-uhci2,ich9-uhci3

    1. cold-plug/hot-unplug USB host device to/from VM
    2. passthrough host usb device with vid/pid or bus/device hostdev
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    usb_index = params.get("usb_index", "0")
    bus_controller = params.get("bus_controller", "")
    usb_model = params.get("usb_model", "")
    start_timeout = int(params.get("start_timeout", "60"))
    usb_hub = "yes" == params.get("usb_hub", "no")
    status_error = "yes" == params.get("status_error", "no")
    passthrough = "yes" == params.get("passthrough", "no")
    vid_pid = "yes" == params.get("vid_pid", "no")
    bus_dev = "yes" == params.get("bus_dev", "no")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    def get_usb_source(lsusb_list):
        """
        calculate a dict of the source xml of usb device based on the output from command lsusb

        :param lsusb_list: a list of the output from command lsusb
        :return: a dict of the source xml of usb device
        """

        logging.debug("lsusb command result: {}".format(lsusb_list))
        source_list = []
        product_list = []
        for line in lsusb_list:
            source = {}
            product = {}
            src = {}
            if re.search("hub", line, re.IGNORECASE):
                continue
            if len(line.split()[5].split(':')) == 2:
                vendor_id, product_id = line.split()[5].split(':')
            if not (vendor_id and product_id):
                test.fail("vendor/product id is not available")
            product['vendor_id'] = "0x" + vendor_id
            product['product_id'] = "0x" + product_id
            product_list.append(product.copy())
            if vid_pid:
                source = product.copy()
            if bus_dev:
                source['bus'] = line.split()[1]
                source['device'] = line.split()[3].rstrip(':')
            source_list.append(source.copy())
        logging.debug("usb device product dict {}, source dict {}".format(product_list, source_list))
        if not source_list or not product_list:
            test.fail("no available usb device in host")
        src['source'] = source_list
        src['product'] = product_list
        return src

    def usb_disk_check(session, src_guest=None):
        """
        :param session: a console session of vm
        :param src_guest: a dict of the source xml of usb device from vm
        """

        # check and write the usb disk
        status, output = session.cmd_status_output("udevadm info /dev/sda")
        if status:
            test.fail("no available usb storage device")
        if session.cmd_status("dd if=/dev/zero of=/dev/sda bs=1M count=100", timeout=300):
            test.fail("usb storage device write fail")

        # check whether passthrough the right usb device
        if passthrough and src_guest:
            output = output.strip().splitlines()
            for guest in src_guest['product']:
                pattern = "ID_MODEL_ID={}".format(guest['product_id'].lstrip("0x"))
                for line in output:
                    if pattern in line:
                        return
            test.fail("passthrough the wrong usb device")

    def usb_device_check(session, src_host=None):
        """
        :param session: a console session of vm
        :param src_host: a dict of the source xml of usb device from host
        """
        if passthrough:
            # check usb device xml
            for addr in src_host['source']:
                if vid_pid:
                    pattern = 'product id="{}"'.format(addr['product_id'])
                if bus_dev:
                    pattern = 'address bus="{}" device="{}"'.format(int(addr['bus']), int(addr['device']))
                vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
                if pattern not in str(vmxml):
                    test.fail("the xml check of usb device fails")

            # check the pid and vid of usb passthrough device in vm
            output = session.get_command_output("lsusb")
            src_guest = get_usb_source(output.strip().splitlines())
            for host in src_host['product']:
                flag = False
                for guest in src_guest['product']:
                    if (guest['product_id'] == host['product_id'] and
                            guest['vendor_id'] == host['vendor_id']):
                        flag = True
                        break
                if not flag:
                    test.fail("usb passthrough device check fail")

        # check usb disk /dev/sda
        if passthrough:
            usb_disk_check(session, src_guest)

    try:
        # remove usb controller/device from xml
        controllers = vmxml.get_devices(device_type="controller")
        for dev in controllers:
            if dev.type == "usb" or dev.type == "pci":
                vmxml.del_device(dev)

        hubs = vmxml.get_devices(device_type="hub")
        for hub in hubs:
            if hub.type_name == "usb":
                vmxml.del_device(hub)

        # assemble the xml of pci/pcie bus
        for model in bus_controller.split(','):
            pci_bridge = Controller('pci')
            pci_bridge.type = "pci"
            pci_bridge.model = model
            vmxml.add_device(pci_bridge)

        # assemble the xml of usb controller
        for model in usb_model.split(','):
            controller = Controller("controller")
            controller.type = "usb"
            controller.index = usb_index
            controller.model = model
            vmxml.add_device(controller)

        if usb_hub:
            hub = Hub("usb")
            vmxml.add_device(hub)

        # install essential package usbutils in host
        pkg = 'usbutils'
        if not utils_package.package_install(pkg):
            test.fail("package usbutils installation fail")

        # assemble the xml of usb passthrough device
        if passthrough:
            hostdevs = vmxml.get_devices(device_type="hostdev")
            for dev in hostdevs:
                vmxml.del_device(dev)
            lsusb_list = process.run('lsusb').stdout_text.splitlines()
            src_host = get_usb_source(lsusb_list)
            for addr in src_host['source']:
                dev = vmxml.get_device_class('hostdev')()
                source_xml = dev.Source()
                dev.mode = 'subsystem'
                dev.hostdev_type = 'usb'
                dev.managed = 'no'
                if vid_pid:
                    source_xml.vendor_id = addr['vendor_id']
                    source_xml.product_id = addr['product_id']
                if bus_dev:
                    source_xml.untyped_address = source_xml.new_untyped_address(**addr)
                dev.source = source_xml
                vmxml.add_device(dev)

        # start vm
        vmxml.sync()
        vm.start()
        session = vm.wait_for_login(timeout=start_timeout)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug("vm xml after starting up {}".format(vmxml))

        # check usb controller in vm
        for model_type in usb_model.split(','):
            model_type = model_type.split('-')[-1].rstrip("1,2,3")
            logging.debug("check usb controller {} in vm".format(model_type))
            if session.cmd_status("dmesg | grep {}".format(model_type)):
                test.fail("usb controller check fail")

        # install package usbutils in vm
        if not utils_package.package_install(pkg, session):
            test.fail("package usbutils installation fails")

        # check usb device
        usb_device_check(session, src_host)

        if passthrough:
            # detach usb passthrough device from vm
            hostdevs = vmxml.get_devices('hostdev')
            logging.debug("detach usb device {}".format(hostdevs))
            for dev in hostdevs:
                if dev.hostdev_type == "usb":
                    virsh.detach_device(vm_name, dev.xml, flagstr="--live", debug=True, ignore_status=False)

            # check the hostdev element in xml after detaching
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            hostdevs = vmxml.get_devices('hostdev')
            logging.debug("hostdevs: {}".format(hostdevs))
            for dev in hostdevs:
                if dev.hostdev_type == "usb":
                    test.fail("detach usb device fail")

    finally:
        if 'session' in locals():
            session.close()
        vmxml_backup.sync()
