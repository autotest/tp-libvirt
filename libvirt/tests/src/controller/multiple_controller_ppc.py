import re
import os
import logging
import platform

from avocado.utils import process

from virttest import virt_vm
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.controller import Controller


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

    if 'ppc' not in platform.machine():
        test.cancel('Only support PPC')

    # Additional disk images.
    disks_img = []

    cntlr_type = params.get('controller_type', None)
    cntlr_model = params.get('controller_model', None)
    with_index = 'yes' == params.get('controller_index', 'yes')
    cntlr_index = params.get('controller_index', None)
    target_index = params.get('target_index', None)
    cntlr_num = int(params.get('controller_num', '0'))
    special_num = params.get('special_num', None)
    addr_str = params.get('address', None)
    device_num = int(params.get('device_num', '0'))
    disk_num = int(params.get('disk_num', '0'))
    memballoon_num = int(params.get('memballoon_num', '0'))
    interface_num = int(params.get('interface_num', '0'))
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    status_error = "yes" == params.get("status_error", "no")

    def match_new_addr():
        """
        Match any device address.
        """
        match = re.match(r"(?P<bus>[0-9]*):(?P<slot>[0-9]*).(?P<function>[0-9])", addr_str)
        if match:
            addr_dict = match.groupdict()
            addr_dict['bus'] = hex(int(addr_dict['bus']))
            addr_dict['slot'] = hex(int(addr_dict['slot']))
            addr_dict['function'] = hex(int(addr_dict['function']))
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
        curcntlr = 0
        while curcntlr < cntlr_num:
            ctrl = Controller(type_name=type)
            if cntlr_model is not None:
                ctrl.model = cntlr_model
                if cntlr_model == 'pci-bridge':
                    ctrl.model_name = {'name': 'pci-bridge'}
            if cntlr_index is not None:
                ctrl.index = cntlr_index
            elif with_index:
                if cntlr_model is not None and cntlr_model == 'pci-bridge':
                    for i in range(1, int(match_new_addr()['bus'], 16) + 1):
                        vm_xml.add_device(add_device('pci', str(i), 'pci-root'))
                    ctrl.index = str(int(match_new_addr()['bus'], 16) + 1)
                else:
                    ctrl.index = str(curcntlr)
            if target_index is not None:
                ctrl.target = {'index': target_index}
            elif with_index:
                if cntlr_model is not None and cntlr_model == 'pci-bridge':
                    ctrl.target = {'chassisNr': str(int(match_new_addr()['bus'], 16) + 1)}
                else:
                    ctrl.target = {'index': str(curcntlr)}
            if addr_str is not None:
                ctrl.address = ctrl.new_controller_address(attrs=match_new_addr())

            logging.debug("Controller XML is:%s", ctrl)
            vm_xml.add_device(ctrl)
            curcntlr += 1
        if special_num:
            spe_num = int(special_num)
            ctrl = Controller(type_name=type)

            if cntlr_model is not None:
                ctrl.model = cntlr_model
            ctrl.index = spe_num
            ctrl.target = {'index': spe_num}
            if addr_str is not None:
                ctrl.address = ctrl.new_controller_address(attrs=match_new_addr())

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
        }

        if cntlr_type == 'pci' and cntlr_model is None:
            fail_patts.append(r"Invalid PCI controller model")
        if cntlr_model is not None and cntlr_model not in known_models[cntlr_type]:
            fail_patts.append(r"Unknown model type")
        if cntlr_model == 'pcie-root':
            fail_patts.append(r"Device requires a standard PCI slot")
        if addr_str == '02:00.0':
            fail_patts.append(r"slot must be >= 1")
        elif addr_str == '02:32.0':
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

    def start_and_check():
        """
        Predict the error message when starting and try to start the guest.
        """
        fail_patts = []
        res = virsh.start(vm_name)
        libvirt.check_result(res, expected_fails=fail_patts)
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
            bus = int(match_new_addr()['bus'], 16)
            if bus > checknum:
                checknum = bus
        if device_num:
            if (device_num + 6) / 31 > checknum:
                checknum = int((device_num + 6) / 31) + 1
        if checknum == 1 and cntlr_num != -1:
            test.fail('Multiple controller is not be used')
        else:
            for i in range(1, checknum):
                restr = r'spapr-pci-host-bridge,index=%s,id=pci.%s' % (i, i)
                if restr not in cmdline:
                    test.fail('The number of %s pci root is not created' % i)

    def check_in_guest():
        session = vm.wait_for_login()
        cmd = 'lspci'
        new_addr = match_new_addr()
        match_line = '%s:00:%s.0 ' % (new_addr['bus'].split('x')[1].zfill(4),
                                      new_addr['slot'].split('x')[1].zfill(2))
        if disk_num:
            match_line += 'SCSI storage controller'
        elif memballoon_num:
            match_line += 'Unclassified device'
        elif interface_num:
            match_line += 'Ethernet controller'
        elif cntlr_type == 'usb':
            match_line += 'USB controller'
        elif cntlr_type == 'pci' and cntlr_model == 'pci-bridge':
            match_line += 'PCI bridge'
        elif cntlr_type == 'virtio-serial':
            match_line += 'Communication controller'
        else:
            test.fail('Unknown device in case config')
        try:
            guest_out = str(session.cmd_output_safe(cmd))
            logging.debug(guest_out)
            if match_line not in guest_out:
                test.fail('Could not find pci device in guest')
        except Exception as e:
            test.fail(e)

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        vm_xml.remove_all_device_by_type('controller')
        remove_usb_devices(vm_xml)
        spe_type = None
        if disk_num:
            spe_type = 'disk'
        elif memballoon_num:
            spe_type = 'memballoon'
        elif interface_num:
            spe_type = 'interface'
        if spe_type:
            spe_ele = vm_xml.xmltreefile.find('/devices/%s/address' % spe_type)
            new_addr = match_new_addr()
            spe_ele.attrib['slot'] = str(new_addr['slot'])
            spe_ele.attrib['bus'] = str(new_addr['bus'])
            vm_xml.xmltreefile.write()
        else:
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

        logging.debug("Test VM XML is %s" % vm_xml)
        ff = open('no', 'w')
        ff.write('vm_xml')
        ff.close()

        if not define_and_check() and status_error:
            logging.debug("Expected define the VM fail, exiting.")
        else:
            incxml = virsh.dumpxml(vm_name)
            logging.debug("The inactive xml:%s" % incxml)
            try:
                if not start_and_check():
                    logging.debug("Expected start the VM fail, exiting.")
                else:
                    check_qemu_cmdline()
                    if addr_str:
                        check_in_guest()
            except virt_vm.VMStartError as detail:
                test.error(detail)

    finally:
        vm_xml_backup.sync()

        for img in disks_img:
            os.remove(img["source"])
            if os.path.exists(img["path"]):
                process.run("umount %s && rmdir %s"
                            % (img["path"], img["path"]), ignore_status=True, shell=True)
