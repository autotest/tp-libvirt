import re
import logging

from avocado.utils import process

from virttest import virsh
from virttest import utils_misc
from virttest import utils_package
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.devices.hub import Hub


def run(test, params, env):
    """
    please insert a usb disk into the host machine before test

    test libvirt usb feature based on the following matrix:
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

    Test scenarios:
    1. by default, cold-plug/hot-unplug usb host device to/from guest
    2. passthrough usb host device with vid/pid or bus/device hostdev
    3. cold-plug/unplug usb host device to/from guest
    4. hot-plug/unplug usb host device to/from guest
    5. by default, cold-plug/hot-unplug usb redirdev device to/from guest
    6. add usb redirdev device by type spicevm or tcp
    7. hot-plug/unplug usb redirdev device to/from guest
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)
    usb_index = params.get("usb_index", "0")
    bus_controller = params.get("bus_controller", "")
    usb_model = params.get("usb_model", "")
    start_timeout = int(params.get("start_timeout", "60"))
    device_name = params.get("device_name", "")
    device_type = params.get("device_type", "")
    device_mode = params.get("device_mode", "")
    port_num = params.get("port_num", "")
    pkgs_host = params.get("pkgs_host", "")
    pkgs_guest = params.get("pkgs_guest", "")
    usb_hub = "yes" == params.get("usb_hub", "no")
    status_error = "yes" == params.get("status_error", "no")
    vid_pid = "yes" == params.get("vid_pid", "no")
    bus_dev = "yes" == params.get("bus_dev", "no")
    hotplug = "yes" == params.get("hotplug", "no")
    coldunplug = "yes" == params.get("coldunplug", "no")
    usb_alias = "yes" == params.get("usb_alias", "no")
    redirdev_alias = "yes" == params.get("redirdev_alias", "no")
    set_addr = params.get("set_addr", "yes")
    ctrl_addr_domain = params.get("ctrl_addr_domain", "")
    ctrl_addr_slot = params.get("ctrl_addr_slot", "")
    ctrl_addr_function = params.get("ctrl_addr_function", "")

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    vmxml_backup = vmxml.copy()

    def get_usb_source(lsusb_list, session=None):
        """
        calculate a dict of the source xml of usb device based on the output from command lsusb

        :param lsusb_list: a list of the output from command lsusb
        :param session: a console session of guest
        :return: a dict of the source xml of usb device
        """

        logging.debug("lsusb command result: {}".format(lsusb_list))
        source_list = []
        product_list = []
        for line in lsusb_list:
            source = {}
            product = {}
            src = {}
            # filter out the usb hub device without vendor/product id
            if re.search("hub", line, re.IGNORECASE):
                continue
            if len(line.split()[5].split(':')) == 2:
                vendor_id, product_id = line.split()[5].split(':')
            if not (vendor_id and product_id):
                test.fail("vendor/product id is not available")
            # filter out the remaining usb hub devcie not catched above
            cmd = "lsusb -v -d {}:{}".format(vendor_id, product_id)
            if session:
                output = session.get_command_output(cmd)
            else:
                output = process.run(cmd).stdout_text
            if "hub" in output:
                continue
            product['vendor_id'] = "0x" + vendor_id
            product['product_id'] = "0x" + product_id
            product_list.append(product.copy())
            if vid_pid:
                source = product.copy()
            if bus_dev:
                source['bus'] = int(line.split()[1])
                source['device'] = int(line.split()[3].rstrip(':'))
            source_list.append(source.copy())
        logging.debug("usb device product dict {}, source dict {}".format(product_list, source_list))
        if not source_list or not product_list:
            test.fail("no available usb device in host")
        src['source'] = source_list
        src['product'] = product_list
        return src

    def usb_disk_check(session, src_guest):
        """
        check usb storage disks passed from host with dd operation and product id

        :param session: a console session of guest
        :param src_guest: a dict of the source xml of usb device from guest
        """

        # check and write the usb disk
        status, output = session.cmd_status_output("udevadm info /dev/sda")
        if status:
            test.fail("no available usb storage device")
        if session.cmd_status("dd if=/dev/zero of=/dev/sda bs=1M count=100", timeout=300):
            test.fail("usb storage device write fail")

        # check whether the guest got the right usb device
        output = output.strip().splitlines()
        for guest in src_guest['product']:
            pattern = "ID_MODEL_ID={}".format(guest['product_id'].lstrip("0x"))
            for line in output:
                if pattern in line:
                    return
        test.fail("usb device {} is NOT found in output {}".format
                  (src_guest['product'], output))

    def usb_device_check(session, src_host):

        """
        check usb devices passed from host with xml file, output of lsusb, and
        usb storage disk.

        :param session: a console session of guest
        :param src_host: a dict of the source xml of usb device from host
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        output = session.get_command_output("lsusb")

        # check usb device xml
        for addr in src_host['source']:
            if device_name == "redirdev":
                pattern = 'redirdev bus="usb" type="{}"'.format(device_type)
            if device_name == "hostdev":
                if vid_pid:
                    pattern = 'product id="{}"'.format(addr['product_id'])
                if bus_dev:
                    pattern = 'address bus="{}" device="{}"'.format(int(addr['bus']), int(addr['device']))
            if pattern not in str(vmxml):
                test.fail("the xml check of usb device fails")

        if device_name == "hostdev" or device_type == "tcp":
            # check the pid and vid of usb passthrough device in guest
            src_guest = get_usb_source(output.strip().splitlines(), session)
            for host in src_host['product']:
                flag = False
                for guest in src_guest['product']:
                    if (guest['product_id'] == host['product_id'] and
                            guest['vendor_id'] == host['vendor_id']):
                        flag = True
                        break
                if not flag:
                    test.fail("the check of usb device in guest fails")

            # check usb disk /dev/sda
                usb_disk_check(session, src_guest)

    def check_alias(device_alias):
        """
        check usb controller alias from qemu command line with xml config file

        :param device_alias: a {model:alias} dict of the usb controller or
                             a {port:alias} dict of the usb redirdev device
        """
        output = process.run("ps -ef | grep {}".format(vm_name), shell=True).stdout_text
        logging.debug('"ps -ef | grep {}" output {}'.format(vm_name, output))
        if usb_alias:
            for model in usb_model.split(','):
                device = (model if model == "qemu-xhci" else
                          ('-').join([model.split('-')[0], "usb", model.split('-')[1]]))
                pattern = ("masterbus={}".format(device_alias['ich9-ehci1'])
                           if "ich9-uhci" in model else "id={}".format(device_alias[model]))
                pattern = "-device {},".format(device) + pattern
                logging.debug("usb controller model {}, pattern {}".format(model, pattern))
                if not re.search(pattern, output):
                    test.fail("the check of controller alias fails")
        if redirdev_alias:
            for alias in device_alias.values():
                pattern = "-device usb-redir,chardev=char{0},id={0}".format(alias)
                if not re.search(pattern, output):
                    test.fail("the check of controller alias fails")

    try:
        # remove usb controller/device from xml
        controllers = vmxml.get_devices(device_type="controller")
        for dev in controllers:
            if dev.type == "usb" or dev.type == "pci":
                vmxml.del_device(dev)

        # clean device address when the address type of device is pci
        for element in vmxml.xmltreefile.findall("/devices/*/address"):
            if element.get('type') == "pci":
                vmxml.xmltreefile.remove(element)
        vmxml.xmltreefile.write()

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
        # find the pci endpoint's name that usb controller will attach
        pci_endpoint = bus_controller.split(",")[-1]
        # find the pci's index that usb controller will attach
        pci_index_for_usb_controller = len(bus_controller.split(",")) - 1
        if usb_model != "none":
            logging.debug("usb_model is not none")
            device_alias = {}
            random_id = process.run("uuidgen").stdout_text.strip()
            # assemble the xml of usb controller
            for i, model in enumerate(usb_model.split(',')):
                controller = Controller("controller")
                controller.type = "usb"
                controller.index = usb_index
                controller.model = model
                if usb_alias:
                    alias_str = "ua-usb" + str(i) + random_id
                    device_alias[model] = alias_str
                    alias = {"name": alias_str}
                    if "ich9" not in model:
                        controller.index = i
                    controller.alias = alias
                # for 'usb_all' case, will not set addr
                if set_addr == "yes":
                    ctrl_addr_dict = {'type': 'pci', 'domain': ctrl_addr_domain, 'bus': '0x0'+str(pci_index_for_usb_controller), 'slot': ctrl_addr_slot, 'function': ctrl_addr_function}
                    if "uhci" in controller.model:
                        ctrl_addr_dict['function'] = "0x0"+str(i)
                    # pcie-switch-downstream-port only supports slot 0
                    if pci_endpoint == "pcie-switch-downstream-port":
                        ctrl_addr_dict['slot'] = "0x00"
                    controller.address = controller.new_controller_address(attrs=ctrl_addr_dict)
                vmxml.add_device(controller)
        else:
            logging.debug("usb_model is none")
        if usb_hub:
            hub = Hub("usb")
            vmxml.add_device(hub)

        # install essential package usbutils in host
        for pkg in pkgs_host.split(','):
            if not utils_package.package_install(pkg):
                test.fail("package {} installation fail".format(pkg))

        # prepare to assemble the xml of usb device
        devs = vmxml.get_devices(device_name)
        for dev in devs:
            if dev.type == device_type:
                vmxml.del_device(dev)
        lsusb_list = process.run('lsusb').stdout_text.splitlines()
        src_host = get_usb_source(lsusb_list)
        dev_list = []

        # assemble the xml of usb passthrough device
        if device_name == "hostdev":
            for addr in src_host['source']:
                device_xml = vmxml.get_device_class(device_name)()
                device_xml.type = device_type
                source_xml = device_xml.Source()
                device_xml.mode = device_mode
                device_xml.managed = 'no'
                if vid_pid:
                    source_xml.vendor_id = addr['vendor_id']
                    source_xml.product_id = addr['product_id']
                if bus_dev:
                    source_xml.untyped_address = source_xml.new_untyped_address(**addr)
                device_xml.source = source_xml
                if hotplug:
                    dev_list.append(device_xml)
                else:
                    vmxml.add_device(device_xml)

        # assemble the xml of usb redirdev device
        if device_name == "redirdev":
            for i, addr in enumerate(src_host['product']):
                device_xml = vmxml.get_device_class(device_name)()
                device_xml.type = device_type
                device_xml.bus = "usb"
                if device_type == "tcp":
                    source_xml = device_xml.Source()
                    source_xml.mode = device_mode
                    source_xml.host = "localhost"
                    port = str(int(port_num)+i)
                    source_xml.service = port
                    source_xml.tls = "no"
                    device_xml.source = source_xml
                    # start usbredirserver
                    vendor_id = addr['vendor_id'].lstrip("0x")
                    product_id = addr['product_id'].lstrip("0x")
                    ps = process.SubProcess("usbredirserver -p {} {}:{}".format
                                            (port, vendor_id, product_id),
                                            shell=True)
                    server_id = ps.start()
                if redirdev_alias:
                    alias_str = "ua-redir" + str(i) + random_id
                    device_alias[port] = alias_str
                    alias = {"name": alias_str}
                    device_xml.alias = alias
                if hotplug:
                    dev_list.append(device_xml)
                else:
                    vmxml.add_device(device_xml)

        # start guest
        vmxml.sync()
        vm.start()
        session = vm.wait_for_login(timeout=start_timeout)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        logging.debug("vm xml after starting up {}".format(vmxml))

        # Check usb controller in guest. By default, usb3(xhci) controller will be added into q35 guest.
        if usb_model == "none" and "q35" in vmxml.os.machine:
            usb_model = "xhci"
        for model_type in usb_model.split(','):
            model_type = model_type.split('-')[-1].rstrip("1,2,3")
            logging.debug("check usb controller {} in guest".format(model_type))
            if session.cmd_status("dmesg | grep {}".format(model_type)):
                test.fail("usb controller check fail")
        if usb_alias or redirdev_alias:
            check_alias(device_alias)

        # install package usbutils in guest
        for pkg in pkgs_guest.split(','):
            if not utils_package.package_install(pkg, session):
                test.fail("package {} installation fails in guest".format(pkg))

        # hotplug usb device
        if hotplug:
            for dev in dev_list:
                virsh.attach_device(vm_name, dev.xml, flagstr="--live",
                                    debug=True, ignore_status=False)
                if device_name == "hostdev":
                    utils_misc.wait_for(lambda: not session.cmd_status(
                                        "lsusb | grep {}".format(dev.source.product_id)), 10)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            logging.debug("vmxml after attaching {}".format(vmxml))

        # check usb device
        usb_device_check(session, src_host)

        # detach usb device from guest
        devs = vmxml.get_devices(device_name)
        if coldunplug:
            vm.destroy()

        for dev in devs:
            if dev.type == device_type:
                if coldunplug:
                    vmxml.del_device(dev)
                else:
                    virsh.detach_device(vm_name, dev.xml, flagstr="--live",
                                        debug=True, ignore_status=False)

        # check the usb device element in xml after detaching
        if coldunplug:
            vmxml.sync()
            vm.start()
            vm.wait_for_login(timeout=start_timeout).close()

        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        devs = vmxml.get_devices(device_name)
        for dev in devs:
            if dev.type == device_type:
                test.fail("detach usb device fail")

    finally:
        if 'session' in locals():
            session.close()
        if 'server_id' in locals():
            process.run("killall usbredirserver")
        vmxml_backup.sync()
