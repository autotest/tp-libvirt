import re
import os
import logging
import platform
import random
import string
import time

from virttest import data_dir
from virttest import virt_vm
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.vm_xml import VMCPUXML
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.devices.disk import Disk
from virttest.libvirt_xml.devices.interface import Interface


def run(test, params, env):
    """
    Test for basic controller device function.

    1) Define the VM with specified controller device and check result meets
       expectation.
    2) Start the guest and check if start result meets expectation
    3) Test the function of started controller device
    4) Shutdown the VM and clean up environment
    """

    def remove_all_addresses(vm_xml):
        """
        Remove all addresses for all devices who has one.
        """
        try:
            for elem in vm_xml.xmltreefile.findall('/devices/*/address'):
                vm_xml.xmltreefile.remove(elem)
        except (AttributeError, TypeError):
            pass  # Element already doesn't exist
        vm_xml.xmltreefile.write()

    def remove_usb_devices(vm_xml):
        """
        Remove all USB devices.
        """
        try:
            for xml in vm_xml.xmltreefile.findall('/devices/*'):
                if xml.get('bus') == 'usb':
                    vm_xml.xmltreefile.remove(xml)
        except (AttributeError, TypeError):
            pass  # Element already doesn't exist
        vm_xml.xmltreefile.write()

    def prepare_local_image(image_filename):
        """
        Prepare a local image.

        :param image_filename: The name to the local image.
        :return: The path to the image file.
        """
        image_format = 'qcow2'
        image_size = '10M'
        image_path = os.path.join(data_dir.get_tmp_dir(), image_filename)
        try:
            image_path = libvirt.create_local_disk("file", image_path, image_size,
                                                   disk_format=image_format)
        except Exception as err:
            test.error("Error happens when prepare local image: %s", err)
        disks_img.append(image_path)
        return image_path

    def prepare_usb_controller(vmxml, index, addr):
        """
        Add usb controllers into vm's xml.

        :param vmxml: The vm's xml.
        """
        # Add disk usb controller(s)
        usb_controller = Controller("controller")
        usb_controller.type = "usb"
        usb_controller.index = str(index)
        usb_controller.model = 'qemu-xhci'
        addr_dict = {"domain": '0x0000', 'funtion': '0x0', 'bus': addr['bus'], 'slot': addr['slot']}
        usb_controller.address = usb_controller.new_controller_address(**{"attrs": addr_dict})
        vmxml.add_device(usb_controller)
        # Redefine domain
        vmxml.sync()

    def prepare_virt_disk_xml(virt_disk_device_target, virt_disk_device_bus, usb_bus=None, virt_disk_bus=None, virt_disk_slot=None):
        """
        Prepare the virt disk xml to be attached/detached.

        :param virt_disk_device_target: The target to the local image.
        :param virt_disk_bus: The bus to the local image.
        :return: The virtual disk xml.
        """
        image_filename = ''.join(random.choice(string.ascii_lowercase) for _ in range(8)) + ".qcow2"
        virt_disk_device = 'disk'
        virt_disk_device_type = 'file'
        virt_disk_device_format = 'qcow2'
        disk_xml = Disk(type_name=virt_disk_device_type)
        disk_xml.device = virt_disk_device
        disk_src_dict = {'attrs': {'file': prepare_local_image(image_filename), 'type_name': 'file'}}
        disk_xml.source = disk_xml.new_disk_source(**disk_src_dict)
        driver_dict = {"name": "qemu", "type": virt_disk_device_format}
        disk_xml.driver = driver_dict
        disk_xml.target = {"dev": virt_disk_device_target,
                           "bus": virt_disk_device_bus}
        if virt_disk_device_bus == 'usb':
            disk_addr_dict = {'bus': str(usb_bus), 'port': '1'}
            disk_xml.new_disk_address(type_name='usb', **{"attrs": disk_addr_dict})
        elif virt_disk_device_bus == 'virtio':
            disk_addr_dict = {'bus': virt_disk_bus, 'slot': virt_disk_slot, 'domain': '0x0000', 'function': '0x0'}
            disk_xml.address = disk_xml.new_disk_address(type_name='pci', **{"attrs": disk_addr_dict})
        return disk_xml

    def prepare_iface_xml(iface_bus, iface_slot):
        """
        Create interface xml file
        """
        iface_xml = Interface(type_name='bridge')
        iface_xml.source = {'bridge': 'virbr0'}
        iface_xml.model = "virtio"
        addr_dict = {'bus': iface_bus, 'slot': iface_slot, 'domain': '0x0000', 'function': '0x0'}
        iface_xml.address = iface_xml.new_iface_address(type_name='pci', **{"attrs": addr_dict})
        return iface_xml

    if 'ppc' not in platform.machine():
        test.cancel('Only support PPC')

    # Additional disk images.
    disks_img = []
    devices_xml = []

    prepare_cntlr = "yes" == params.get('prepare_controller', "no")
    cntlr_type = params.get('controller_type')
    cntlr_model = params.get('controller_model', '')
    with_index = 'yes' == params.get('controller_index', 'yes')
    cntlr_index = params.get('controller_index')
    cntlr_node = params.get('controller_node')
    target_index = params.get('target_index')
    cntlr_num = int(params.get('controller_num', '0'))
    cntlr_cur = int(params.get('controller_current', '0'))
    special_num = params.get('special_num')
    addr_str = params.get('address')
    if addr_str:
        addr_str = eval(addr_str)
    device_num = int(params.get('device_num', '0'))
    device_list = params.get('device_list', '')
    if device_list:
        device_list = eval(device_list)
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    qemu_cmd_check = "yes" == params.get("qemu_cmd_check", "no")
    status_error = "yes" == params.get("status_error", "no")
    numa = "yes" == params.get("numa", "no")
    with_define = 'yes' == params.get("with_define", "no")
    coldplug = "yes" == params.get("coldplug", "no")
    hotplug = "yes" == params.get("hotplug", "no")
    hotunplug = "yes" == params.get("hotunplug", "no")

    def check_index_in_xml(xml):
        """
        Check the used target in guest's xml
        :param xml:  The guest's xml
        :return:  A dict of result
        """
        result = {'sd': 'a', 'vd': 'a', 'index': 1}
        disk_list = xml.xmltreefile.findall("devices/disk/target")
        for disk_target in disk_list:
            dev = disk_target.attrib['dev']
            if dev[-1] >= result[dev[0:-1]]:
                result[dev[0:-1]] = chr(ord(dev[-1]) + 1)
        controller_list = xml.xmltreefile.findall("devices/controller")
        for controller in controller_list:
            if int(controller.get('index')) >= result['index']:
                result['index'] = int(controller.get('index')) + 1
        return result

    def enumerate_index(index_dict, index_key):
        index = index_dict[index_key]
        result = index_key + index if index_key in ['sd', 'vd'] else str(index)
        if index_key in ['sd', 'vd'] and index == 'z':
            index = 'aa'
        elif index_key in ['sd', 'vd']:
            if len(index) > 1:
                index = index[0] + chr(ord(index[-1]) + 1)
            else:
                index = chr(ord(index) + 1)
        else:
            index += 1
        index_dict[index_key] = index
        return result

    def match_new_addr(address):
        """
        Match any device address.
        """
        logging.info("The address is:%s" % address)
        match = re.match(r"(?P<bus>[0-9a-f]*):(?P<slot>[0-9a-f]*).(?P<function>[0-9a-f])", address)
        if match:
            addr_dict = match.groupdict()
            addr_dict['bus'] = hex(int(addr_dict['bus'], 16))
            addr_dict['slot'] = hex(int(addr_dict['slot'], 16))
            addr_dict['function'] = hex(int(addr_dict['function'], 16))
            addr_dict['domain'] = '0x0000'
            return addr_dict
        return None

    def add_device(type="usb", index="0", model="qemu-xhci"):
        """
        Add new device.
        """
        newcontroller = Controller("controller")
        newcontroller.type = type
        newcontroller.index = index
        newcontroller.model = model
        logging.debug("New controller is:%s", newcontroller)
        return newcontroller

    def setup_controller_xml():
        """
        Prepare controller devices of VM XML according to params.
        """

        if cntlr_type is None:
            type = 'pci'
        else:
            type = cntlr_type
        curcntlr = cntlr_cur
        while curcntlr < cntlr_num:
            ctrl = Controller(type_name=type)
            if cntlr_node:
                ctrl.node = cntlr_node
            if cntlr_model:
                ctrl.model = cntlr_model
                if cntlr_model == 'pci-bridge':
                    ctrl.model_name = {'name': 'pci-bridge'}
            if cntlr_index is not None:
                ctrl.index = cntlr_index
            elif with_index:
                if cntlr_model == 'pci-bridge':
                    for i in range(1, int(match_new_addr(addr_str[curcntlr])['bus'], 16) + 1):
                        vm_xml.add_device(add_device('pci', str(i), 'pci-root'))
                    ctrl.index = str(int(match_new_addr(addr_str[curcntlr])['bus'], 16) + 1)
                else:
                    ctrl.index = str(curcntlr)
            if target_index is not None:
                ctrl.target = {'index': target_index}
            elif with_index:
                if cntlr_model == 'pci-bridge':
                    ctrl.target = {'chassisNr': str(int(match_new_addr(addr_str[curcntlr])['bus'], 16) + 1)}
                else:
                    ctrl.target = {'index': str(curcntlr)}
            if addr_str is not None:
                for address in addr_str:
                    ctrl.address = ctrl.new_controller_address(attrs=match_new_addr(address))

            logging.debug("Controller XML is:%s", ctrl)
            vm_xml.add_device(ctrl)
            curcntlr += 1
        if special_num:
            spe_num = int(special_num)
            ctrl = Controller(type_name=type)

            if cntlr_model:
                ctrl.model = cntlr_model
            ctrl.index = spe_num
            ctrl.target = {'index': spe_num}
            if addr_str is not None and cntlr_model != 'pci-root':
                for address in addr_str:
                    ctrl.address = ctrl.new_controller_address(attrs=match_new_addr(address))

            logging.debug("Controller XML is:%s", ctrl)
            vm_xml.add_device(ctrl)

    def define_and_check():
        """
        Predict the error message when defining and try to define the guest
        with testing XML.
        """
        fail_patts = []
        known_models = {
            'pci': ['pci-root', 'pci-bridge'],
            'virtio-serial': [],
            'usb': ['qemu-xhci'],
            'scsi': ['virtio-scsi'],
        }
        if status_error:
            if cntlr_type == 'pci' and cntlr_model:
                fail_patts.append(r"Invalid PCI controller model")
            if cntlr_type and cntlr_model not in known_models[cntlr_type]:
                fail_patts.append(r"Unknown model type")
            if cntlr_model == 'pcie-root':
                fail_patts.append(r"Device requires a standard PCI slot")
            if addr_str and '02:00.0' in addr_str:
                fail_patts.append(r"slot must be >= 1")
            elif addr_str and '02:32.0' in addr_str:
                fail_patts.append(r"must be <= 0x1F")
            if cntlr_num > 31 and cntlr_type == 'pci':
                fail_patts.append(r"out of range - must be 0-30")
            if cntlr_index and target_index:
                if (int(cntlr_index) != 0) ^ (int(target_index) != 0):
                    fail_patts.append(r"Only the PCI controller with index 0 can have target index 0")

            # isdigit will return false on negative number, which just meet the
            # requirement of this test.
            if cntlr_index is not None and not cntlr_index.isdigit():
                fail_patts.append(r"Cannot parse controller index")

        vm_xml.undefine()
        res = vm_xml.virsh.define(vm_xml.xml)
        libvirt.check_result(res, expected_fails=fail_patts)
        return not res.exit_status

    def plug_the_devices(attach_options, dev_index):
        for index, dev in enumerate(device_list):
            if addr_str:
                new_addr = match_new_addr(addr_str[index])
            if dev == 'disk':
                disk_xml = prepare_virt_disk_xml(enumerate_index(dev_index, 'vd'), 'virtio',
                                                 virt_disk_bus=new_addr['bus'],
                                                 virt_disk_slot=new_addr['slot'])
                logging.debug("The disk xml is: %s" % disk_xml)
                result = virsh.attach_device(vm_name, disk_xml.xml,
                                             flagstr=attach_options,
                                             ignore_status=True, debug=True)
                libvirt.check_exit_status(result, status_error)
                devices_xml.append(disk_xml)
            elif dev == 'usb':
                disk_xml = prepare_virt_disk_xml(enumerate_index(dev_index, 'sd'), 'usb',
                                                 usb_bus=enumerate_index(dev_index, 'index'))
                logging.debug("The disk xml is: %s" % disk_xml)
                result = virsh.attach_device(vm_name, disk_xml.xml,
                                             flagstr=attach_options,
                                             ignore_status=True, debug=True)
                libvirt.check_exit_status(result, status_error)
                devices_xml.append(disk_xml)
            elif dev == 'interface':
                iface_xml = prepare_iface_xml(iface_bus=new_addr['bus'], iface_slot=new_addr['slot'])
                logging.debug("The nic xml is: %s" % iface_xml)
                result = virsh.attach_device(vm_name, iface_xml.xml,
                                             flagstr=attach_options,
                                             ignore_status=True, debug=True)
                libvirt.check_exit_status(result, status_error)
                devices_xml.append(iface_xml)

    def start_and_check():
        """
        Predict the error message when starting and try to start the guest.
        """
        fail_patts = []
        res = virsh.start(vm_name)
        libvirt.check_result(res, expected_fails=fail_patts)
        vm.wait_for_login().close()
        return not res.exit_status

    def check_qemu_cmdline():
        """
        Check domain qemu command line against expectation.
        """
        cmdline = open('/proc/%s/cmdline' % vm.get_pid()).read()
        logging.debug('Qemu command line: %s', cmdline)
        cmdline_list = cmdline.split('\x00')
        # TODO
        checknum = 1
        if cntlr_num > checknum:
            checknum = cntlr_num
        if special_num and int(special_num) > checknum:
            checknum = int(special_num)
        if addr_str:
            for address in addr_str:
                bus = int(match_new_addr(address)['bus'], 16)
                if bus > checknum:
                    checknum = bus
        if device_num:
            if (device_num + 6) / 31 > checknum:
                checknum = int((device_num + 6) / 31) + 1
        if checknum == 1 and cntlr_num != -1:
            test.fail('Multiple controller is not be used')
        else:
            for i in range(1, checknum):
                restr = r'spapr-pci-host-bridge,index=%s' % i
                if restr not in cmdline:
                    test.fail('The number of %s pci root is not created' % i)

    def check_in_guest_pci_with_addr(check_flag=True):
        def generate_match_line(index):
            match_dict = {
                'disk': 'SCSI storage controller',
                'memballoon': 'Unclassified device',
                'interface': 'Ethernet controller',
                'usb': 'USB controller',
                'pci-bridge': 'PCI bridge',
                'serial': 'Communication controller'
            }
            new_addr = match_new_addr(addr_str[index])
            match_line = '00(0[1-9a-f]|1[0-9a-f]):00:%s.0 ' % new_addr['slot'].split('x')[1].zfill(2)
            if device_list[index] in match_dict.keys():
                match_line += match_dict[device_list[index]]
            else:
                test.fail('Unknown device(%s) in case config' % device_list[index])
            return match_line

        session = vm.wait_for_login()
        cmd = 'lspci'
        try:
            guest_out = str(session.cmd_output_safe(cmd))
            logging.debug(guest_out)
            for i in range(len(addr_str)):
                match_line = generate_match_line(i)
                times = 0
                while True:
                    if not re.search(match_line, guest_out) and check_flag:
                        if times < 5:
                            time.sleep(5)
                            times += 1
                            guest_out = str(session.cmd_output_safe(cmd))
                            logging.debug(guest_out)
                        else:
                            test.fail('Could not find pci device in guest')
                    elif re.search(match_line, guest_out) and not check_flag and device_list[i] != 'usb':
                        if times < 5:
                            time.sleep(5)
                            times += 1
                            guest_out = str(session.cmd_output_safe(cmd))
                            logging.debug(guest_out)
                        else:
                            test.fail('Could find pci device after detach')
                    else:
                        break
        except Exception as e:
            session.close()
            test.fail(e)
        if cntlr_node and check_flag:
            cmd = "lspci -vv -s %s | grep 'NUMA node:' | grep -o [0-9]*"
            for addr in addr_str:
                guest_out = str(session.cmd_output_safe(cmd % addr)).strip()
                if str(guest_out) != cntlr_node:
                    test.fail('No plug on the right node')
        session.close()

    def check_in_guest_pci_with_num():
        if cntlr_type == 'scsi':
            check_line = 'SCSI storage controller'
        else:
            return

        session = vm.wait_for_login()
        cmd = "lspci | grep '%s' | wc -l" % check_line
        try:
            guest_out = str(session.cmd_output_safe(cmd))
            logging.debug(guest_out)
            if int(guest_out) != int(cntlr_num) + int(default_pci):
                test.fail('The number of controller is not right')
        except Exception as e:
            test.fail(e)
        finally:
            session.close()

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        vm_xml.remove_all_device_by_type('controller')
        remove_usb_devices(vm_xml)
        if int(cntlr_num) > 10:
            virsh.start(vm_name)
            session = vm.wait_for_login()
            cmd = "lspci | grep 'SCSI storage controller' | wc -l"
            try:
                default_pci = str(session.cmd_output_safe(cmd))
            except Exception as e:
                test.fail(e)
            finally:
                session.close()
                virsh.destroy(vm_name)
        spe_device = False
        if numa:
            if vm_xml.xmltreefile.find('/cpu'):
                vmcpuxml = vm_xml.cpu
                if not vmcpuxml.xmltreefile.find('numa'):
                    vmcpuxml.xmltreefile.create_by_xpath('numa')
            else:
                vmcpuxml = VMCPUXML()
                vmcpuxml.xml = '<cpu><numa/></cpu>'
            vmcpuxml.numa_cell = vmcpuxml.dicts_to_cells(
                [{'id': '0', 'cpus': '0', 'memory': '1048576'},
                 {'id': '1', 'cpus': '1', 'memory': '1048576'}])
            vm_xml.xmltreefile.write()
        if with_define:
            if addr_str:
                for i in range(len(addr_str)):
                    if device_list[i] in ['disk', 'memballoon', 'interface']:
                        spe_device = True
                        spe_ele = vm_xml.xmltreefile.find('/devices/%s/address' % device_list[i])
                        new_addr = match_new_addr(addr_str[i])
                        spe_ele.attrib['slot'] = str(new_addr['slot'])
                        spe_ele.attrib['bus'] = str(new_addr['bus'])
                        vm_xml.xmltreefile.write()
            if not spe_device:
                remove_all_addresses(vm_xml)

            if cntlr_num or special_num:
                setup_controller_xml()

            if device_num:
                newdev_list = []
                for i in range(1, device_num + 1):
                    newdev_list.append(add_device(index=str(i)))

                dev_list = vm_xml.get_devices()
                dev_list.extend(newdev_list)
                vm_xml.set_devices(dev_list)

        if prepare_cntlr:
            setup_controller_xml()

        if hotplug or coldplug:
            if 'usb' in device_list:
                vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
                for index, dev in enumerate(device_list):
                    if dev == 'usb':
                        new_addr = match_new_addr(addr_str[index])
                        dev_index = check_index_in_xml(vm_xml)
                        prepare_usb_controller(vm_xml, dev_index['index'], new_addr)
                        vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)

        logging.debug("Test VM XML is %s" % vm_xml)

        if not define_and_check() and status_error:
            logging.debug("Expected define the VM fail, exiting.")
        else:
            incxml = virsh.dumpxml(vm_name).stdout
            logging.debug("The inactive xml:%s" % incxml)
            if coldplug:
                attach_options = "--config"
                dev_index = check_index_in_xml(vm_xml)
                plug_the_devices(attach_options, dev_index)
                incxml = virsh.dumpxml(vm_name)
                logging.debug("The xml after cold plug:%s" % incxml)
            try:

                if not start_and_check() and status_error:
                    logging.debug("Expected start the VM fail, exiting.")
                else:
                    if hotplug:
                        attach_options = "--live"
                        dev_index = check_index_in_xml(vm_xml)
                        plug_the_devices(attach_options, dev_index)
                        incxml = virsh.dumpxml(vm_name)
                        logging.debug("The xml after hot plug:%s" % incxml)
                    if qemu_cmd_check:
                        check_qemu_cmdline()
                    if addr_str:
                        check_in_guest_pci_with_addr()
                    if int(cntlr_num) > 10:
                        check_in_guest_pci_with_num()
            except virt_vm.VMStartError as detail:
                test.error(detail)

        if hotunplug:
            logging.debug("Try to hot unplug")
            detach_options = "--live"
            for xml in devices_xml:
                result = virsh.detach_device(vm_name, xml.xml, flagstr=detach_options, ignore_status=True, debug=True)
                libvirt.check_exit_status(result, status_error)
            if addr_str:
                check_in_guest_pci_with_addr(False)

    finally:
        vm_xml_backup.sync()

        for img in disks_img:
            os.remove(img)
