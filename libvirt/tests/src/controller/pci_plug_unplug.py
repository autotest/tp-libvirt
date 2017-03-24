import re
import os
import time
import logging

from virttest import virt_vm
from virttest import virsh
from virttest import data_dir
from virttest import utils_test
from virttest.utils_test import libvirt
from virttest import utils_net
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.interface import Interface
from virttest.libvirt_xml.devices.controller import Controller
from virttest.qemu_storage import QemuImg


def run(test, params, env):
    """
    Test for PCIe devices functions.

    1) Define the VM w/o specified controller device and check result meets
       expectation.
    2) Start the guest and check if start result meets expectation
    3) Test the function of started devices
    4) Shutdown the VM and clean up environment
    """

    def remove_from_xml(attrs):
        """
        Remove from guest XML according to given patterns.

        :param attrs: The XML elements to be searched
        :return: True if success, otherwise, False
        """
        try:
            for elem in vm_xml.xmltreefile.findall(attrs):
                vm_xml.xmltreefile.remove(elem)
        except (AttributeError, TypeError), details:
            logging.error("Fail to remove elements '%s': %s", attrs, details)
            return False
        vm_xml.xmltreefile.write()
        return True

    def add_ifaces_devices(ifaces_list,
                           mac_addr_prefix=None,
                           remove_existing=True):
        """
        Add interface devices to guest XML

        :param ifaces_list: interface configuration to be added
        :param mac_addr_prefix: Prefix used to produce mac address
        :param remove_existing: Flag to remove existing interface devices

        """
        logging.debug("Add ifaces devices to guest XML")
        if remove_existing:
            remove_from_xml('/devices/interface')
        count = 2
        for iface_dict in ifaces_list:
            iface_type = iface_dict['iface_type']
            iface = Interface(type_name=iface_type)
            iface.model = iface_dict['iface_model']
            iface.source = iface_dict['iface_source']
            if iface_dict.has_key("iface_address") and iface_dict['iface_address']:
                iface.address = iface.new_iface_address(attrs=iface_dict['iface_address'])
            else:
                addr_suffix = "%02x" % count
                iface.mac_address = '%s:%s' % (mac_addr_prefix, addr_suffix)
                addr_dict = {}
                addr_dict['bus'] = '0x%02x' % count
                count = count + 1
                addr_dict['slot'] = '0x01'
                addr_dict['function'] = '0x0'
                addr_dict['domain'] = '0x0000'
                iface.address = iface.new_iface_address(attrs=addr_dict)
            logging.debug(iface.get_xmltreefile())
            vm_xml.add_device(iface)

    def setup_os_xml():
        """
        Prepare os part of VM XML according to params.
        """
        osxml = vm_xml.os
        orig_machine = osxml.machine
        if '-' in orig_machine:
            suffix = orig_machine.split('-')[-1]
            new_machine = '-'.join(('pc', os_machine, suffix))
        else:
            if os_machine == 'i440fx':
                new_machine = 'pc'
            else:
                new_machine = os_machine
        osxml.machine = new_machine
        vm_xml.os = osxml

    def setup_controller_xml(cntl_type, cntl_model, cntl_index, cntl_busNr=None, cntl_addr_str=None):
        """
        Setup controller devices of VM XML according to params.

        :param cntl_type: The controller type
        :param cntl_model: The controller model
        :param cntl_index: The controller index
        :param cntl_busNr: The busNr number
        :param cntl_addr_str: The controller address

        """

        ctrl = Controller(type_name=cntl_type)

        if cntl_model:
            ctrl.model = cntl_model
        if cntl_index:
            ctrl.index = cntl_index
        if cntl_model and cntl_model == 'pcie-expander-bus' and cntl_busNr:
            ctrl.target = {'busNr': cntl_busNr}

        if cntl_addr_str:
            match = re.match(r"(?P<bus>[0-9]*):(?P<slot>[0-9a-f]*).(?P<function>[0-9])", cntl_addr_str)
            if match:
                addr_dict = match.groupdict()
                addr_dict['bus'] = hex(int(addr_dict['bus'], 16))
                addr_dict['slot'] = hex(int(addr_dict['slot'], 16))
                addr_dict['function'] = hex(int(addr_dict['function'], 16))
                addr_dict['domain'] = '0x0000'
                ctrl.address = ctrl.new_controller_address(attrs=addr_dict)

        logging.debug("Controller XML is:%s", ctrl)

        vm_xml.add_device(ctrl)

    def get_controller_addr(cntl_type=None, cntl_model=None, cntl_index=None):
        """
        Get the address of controller from VM XML as a string with
        format "bus:slot.function".

        :param cntl_type: controller type
        :param cntl_model: controller model
        :param cntl_index: controller index

        :return: an address string of the specified controller
        :raise: exceptions.TestError if the controller is not found
        """
        addr_str = None
        cur_vm_xml = VMXML.new_from_dumpxml(vm_name)

        for elem in cur_vm_xml.devices.by_device_tag('controller'):
            # logging.debug("%s,%s,%s", elem.type, elem.model, elem.index)
            if (elem.type == cntl_type and
               elem.model == cntl_model and
               elem.index == cntl_index):
                addr_elem = elem.address
                if addr_elem is None:
                    test.error("Can not find 'Address' "
                               "element for the device")

                bus = int(addr_elem.attrs.get('bus'), 0)
                slot = int(addr_elem.attrs.get('slot'), 0)
                func = int(addr_elem.attrs.get('function'), 0)
                addr_str = '%02x:%02x.%1d' % (bus, slot, func)
                logging.debug("Found controller address: '%s' for "
                              "type='%s', model='%s', index='%s'",
                              addr_str, cntl_type, cntl_model, cntl_index)
                break

        return addr_str

    def define_and_check():
        """
        Define the guest with testing XML.
        """

        vm_xml.undefine()
        res = vm_xml.virsh.define(vm_xml.xml)
        libvirt.check_result(res)
        return not res.exit_status

    def start_and_check():
        """
        Start the guest and check result
        """

        res = virsh.start(vm_name)
        libvirt.check_result(res)
        return not res.exit_status

    def test_ping(eth_ip_list):
        """
        Do ping test to the given IP

        :param eth_ip_list: The list includes the IPs

        :raise: exceptions.TestFail if ping fails
        """

        for vm_ip in eth_ip_list:
            s_ping, o_ping = utils_test.ping(vm_ip,
                                             count=10,
                                             timeout=30)
            logging.info(o_ping)
            if s_ping != 0:
                test.fail("%s did not respond with ip '%s'." % (vm_name, vm_ip))

    def add_vm_disk():
        """
        Add VM disk to the guest

        """

        logging.debug("Add a VM disk")
        disk_xml = Disk(type_name=disk_type)
        driver_dict = {"name": driver_name,
                       "type": image_format,
                       "cache": driver_cache}
        disk_xml.driver = driver_dict
        disk_xml.target = {"dev": disk_dev,
                           "bus": disk_bus}
        disk_xml.device = "disk"
        disk_addr_dict = {}
        if disk_bus == 'virtio':
            disk_addr_dict = {"bus": '0x05',
                              "slot": '0x01',
                              "function": '0x0',
                              "domain": "0x0000",
                              "type": 'pci'}
        elif disk_bus == 'sata' or disk_bus == 'scsi':
            disk_addr_dict = {"bus": '0',
                              "target": '0',
                              "unit": '0',
                              "controller": cntlr_index,
                              "type": 'drive'}
        elif disk_bus == 'usb':
            disk_addr_dict = {"bus": cntlr_index,
                              "type": 'usb',
                              "port": '1'}
        disk_xml.address = disk_xml.new_disk_address(attrs=disk_addr_dict)

        image_name_with_suffix = "%s.%s" % (image_name, image_format)
        source_file_full_path = os.path.join(data_dir.get_tmp_dir(),
                                             image_name_with_suffix)
        disk_source_dict = {'file': source_file_full_path}
        disk_xml.source = disk_xml.new_disk_source(attrs=disk_source_dict)
        # Create the source image file in temp directory
        params_img = {}
        params_img['image_name'] = image_name
        params_img['image_size'] = image_size
        params_img['create_with_dd'] = create_with_dd
        params_img['image_format'] = image_format
        params_img['vm_type'] = params.get("vm_type", "libvirt")
        qemu_tool = QemuImg(params_img, data_dir.get_tmp_dir(), '')
        qemu_tool.create(params_img)

        vm_xml.add_device(disk_xml)
        vm_xml.sync()

    def check_vm_disk(vm_session, disk):
        """
        Check the given disk can be operated normally

        :param vm_session: session of logging on the guest
        :param disk: the disk name to be checked, like '/dev/sda'

        :raise: exceptions.TestFail if commands' execution fails
        """

        cmd = "mkfs.xfs %s -f" % disk
        cmd = "%s && mount %s /mnt" % (cmd, disk)
        cmd = "%s && echo hello > /mnt/hello && cat /mnt/hello" % cmd
        cmd = "%s && umount /mnt" % cmd
        logging.debug("Execute command on the VM: %s", cmd)
        if not vm_session:
            vm_session = vm.wait_for_login()
        status, output = vm_session.cmd_status_output(cmd)
        logging.debug("Command '%s' output is: %s", cmd, output)
        if status:
            test.fail("Failed to check VM disk:%s" % output)

    # Global variables
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    vm_session = None

    os_machine = params.get('os_machine', 'i440fx')
    cntlr_type = params.get('controller_type', None)
    cntlr_model = params.get('controller_model', None)
    cntlr_index = params.get('controller_index', None)
    cntlr_addr = params.get('controller_address', None)
    remove_address = "yes" == params.get("remove_address", "no")
    setup_controller = "yes" == params.get("setup_controller", "no")
    #multi_nic = "yes" == params.get("multi_nic", "no")
    nic_num = params.get("nic_num", None)
    setup_pxb_pcie = "yes" == params.get("setup_pxb_pcie", "no")
    pci_bridge_num = params.get("pci_bridge_num", None)
    mac_addr_prefix = params.get('mac_addr_prefix', None)
    single_nic = "yes" == params.get('single_nic', "no")
    busNr = params.get('busNr', None)
    check_nic = "yes" == params.get("check_nic", "no")
    check_block = "yes" == params.get("check_block", "no")
    add_disk = "yes" == params.get("add_disk", "no")

    # Get the plugged disk attributes
    disk_type = params.get("disk_type", "file")
    driver_name = params.get("driver_name", "qemu")
    image_format = params.get('image_fileformat', "qcow2")
    driver_cache = params.get('driver_cache', "none")
    disk_dev = params.get('disk_dev', "vdb")
    disk_bus = params.get('disk_bus', 'virtio')
    image_name = params.get('image_filename', 'disk_image')
    image_size = params.get('image_filesize', '500M')
    create_with_dd = params.get('create_with_dd', 'no')

    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    vm_old_disks = []
    vm_new_disks = []

    try:
        vm_xml.remove_all_device_by_type('controller')
        if remove_address:
            remove_from_xml('/devices/*/address')
        setup_os_xml()
        if setup_controller:
            if pci_bridge_num and int(pci_bridge_num) > 0:
                setup_controller_xml('pci', 'pcie-root', '0')
                setup_controller_xml('pci', 'dmi-to-pci-bridge', '1')
                setup_controller_xml('pci', 'pci-bridge', '2')
                for count in range(3, int(pci_bridge_num) + 3):
                    setup_controller_xml('pci', 'pci-bridge', str(count))
            if setup_pxb_pcie:
                setup_controller_xml('pci', 'pcie-root', '0')
                setup_controller_xml('pci', 'dmi-to-pci-bridge', '1')
                setup_controller_xml('pci', 'pci-bridge', '2')
                setup_controller_xml('pci', 'pcie-expander-bus', '3',
                                     busNr, '00:02.0')
                setup_controller_xml('pci', 'dmi-to-pci-bridge', '4',
                                     None, '03:00.0')
                setup_controller_xml('pci', 'pci-bridge', '5',
                                     None, '04:00.0')
            if cntlr_type and cntlr_index:
                setup_controller_xml(cntlr_type, cntlr_model,
                                     cntlr_index, busNr, cntlr_addr)

        if nic_num:
            iface_type = params.get('iface_type', 'network')
            iface_network = params.get('iface_network', 'default')
            iface_models = params.get('iface_models', 'virtio')
            ifaces_list = []
            count_nic = 0
            while count_nic < int(nic_num):
                iface_model_list = iface_models.split(' ')
                model_num = len(iface_model_list)
                one_nic_dict = {'iface_type': iface_type,
                                'iface_model': iface_model_list[count_nic % model_num],
                                'iface_source': {'network': iface_network}}
                ifaces_list.append(one_nic_dict)
                count_nic += 1
            add_ifaces_devices(ifaces_list, mac_addr_prefix)

        if single_nic:
            iface_type = params.get('iface_type', 'network')
            iface_network = params.get('iface_network', 'default')
            iface_model = params.get('iface_model', 'virtio')
            iface_address = params.get('iface_address', None)
            addr_dict = None
            if iface_address:
                match = re.match(r"(?P<bus>[0-9]*):(?P<slot>[0-9a-f]*).(?P<function>[0-9])", iface_address)
                if match:
                    addr_dict = match.groupdict()
                    single_nic_list = [
                     {'iface_type': iface_type, 'iface_model': iface_model,
                      'iface_source': {'network': iface_network},
                      'iface_address': {'bus': hex(int(addr_dict['bus'], 16)),
                                        'slot': hex(int(addr_dict['slot'], 16)),
                                        'function': hex(int(addr_dict['function'], 16)),
                                        'domain': '0x0000'}}]
                    add_ifaces_devices(single_nic_list, mac_addr_prefix)

        if not define_and_check():
            logging.debug("Can't define the VM, exiting.")
            return

        if add_disk:
            # Get the original VM's disks
            if vm.is_dead():
                try:
                    vm.start()
                except virt_vm.VMStartError, detail:
                    test.fail(detail)

            vm_old_disks = vm.get_disks()
            vm.destroy()
            add_vm_disk()
        try:
            if not start_and_check():
                logging.debug("Can't start the VM, exiting.")
                return
        except virt_vm.VMStartError, detail:
            test.fail(detail)

        vm_xml = VMXML.new_from_dumpxml(vm_name)
        logging.debug("Test VM XML after starting:\n%s", vm_xml)

        vm_session = vm.wait_for_login(serial=True)

        ctl_addr_list = []
        eth_addr_list = []
        eth_ip_list = []

        if pci_bridge_num and int(pci_bridge_num) > 0:
            # Get all the controller in guest XML
            # and expect them to exist in the guest
            for inx in range(2, int(pci_bridge_num) + 3):
                ctl_addr_str = get_controller_addr(cntl_type='pci',
                                                   cntl_model='pci-bridge',
                                                   cntl_index=str(inx))

                ctl_addr_list.append(ctl_addr_str)
            logging.debug(ctl_addr_list)

            cmd = 'lspci |grep "PCI bridge"'
            output = vm_session.cmd_output(cmd)
            logging.debug("Command '%s' output is: %s", cmd, output)

            for ctl in ctl_addr_list:
                if not re.search(ctl, output):
                    test.fail("Can not find the controller '%s' "
                              "in guest" % ctl)
                else:
                    logging.debug("Get the expected controller:'%s'", ctl)
        if setup_pxb_pcie:
            expect_msgs = ['Bus: primary=%02x, secondary=%02x'
                           % (int(busNr) + 1, int(busNr) + 2),
                           '%02x:01.0' % (int(busNr) + 2)]
            cmd = 'lspci -v'
            output = vm_session.cmd_output(cmd)
            logging.debug("Command '%s' output is: %s", cmd, output)
            for msg in expect_msgs:
                if not re.search(msg, output):
                    test.fail("Can't get the expected "
                              "message '%s'" % msg)
                else:
                    logging.debug("Get the expected message:'%s'", msg)
        if check_nic:
            eth_dict = vm_xml.get_iface_all()
            for mac, eth in eth_dict.items():
                bus = int(eth.find('address').get('bus'), 16)
                slot = int(eth.find('address').get('slot'), 16)
                func = int(eth.find('address').get('function'), 16)
                if setup_pxb_pcie:
                    eth_addr_str = '%02x:%02x.%1d' % (int(busNr) + 2,
                                                      slot,
                                                      func)
                else:
                    eth_addr_str = '%02x:%02x.%1d' % (bus, slot, func)

                eth_addr_list.append(eth_addr_str)
            logging.debug("The ethernet address list: %s", eth_addr_list)

            cmd = 'lspci |grep "Ethernet controller"'
            output = vm_session.cmd_output(cmd)
            logging.debug("Command '%s' output is: %s", cmd, output)

            for eth in eth_addr_list:
                if not re.search(eth, output):
                    test.fail("Can not find the ethernet controller "
                              "'%s' in guest" % eth)
                else:
                    logging.debug("Get the expected Ethernet controller "
                                  "in VM:'%s'.", eth)

            utils_net.restart_guest_network(vm_session)
            # Wait and get all the IP address of all NICs
            for mac in eth_dict.keys():
                times = 0
                ip_addr = None
                while (times < 20):
                    ip_addr = utils_net.get_guest_ip_addr(vm_session, mac)
                    if not ip_addr:
                        times += 1
                        time.sleep(15)
                        continue
                    else:
                        eth_ip_list.append(ip_addr)
                        break
                if (times == 20 and not ip_addr):
                    test.fail("Can't get ip address for mac '%s'" % mac)
            logging.debug("Get ethernet IP list:%s", eth_ip_list)
            # Ping test for those IPs of all NICs
            test_ping(eth_ip_list)

        if check_block:
            vm_new_disks = vm.get_disks()
            logging.debug("VM disks before adding disk:%s", vm_old_disks)
            logging.debug("VM disks after adding disk:%s", vm_new_disks)
            vm_new_disk = None
            # What is the new disk
            for vm_new_disk in vm_new_disks:
                if vm_old_disks.count(vm_new_disk) <= 0:
                    # This is a new disk
                    logging.debug("The newly added VM disk:%s",
                                  vm_new_disk)
                    break
            if not vm_new_disk:
                test.fail("Can not find the expected new disk")
            check_vm_disk(vm_session, vm_new_disk)

    finally:
        vm_xml_backup.sync()
        data_dir.clean_tmp_files()
