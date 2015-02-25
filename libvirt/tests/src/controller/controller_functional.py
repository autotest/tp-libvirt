import re
import logging
from autotest.client.shared import error
from virttest import virt_vm
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.devices.address import Address


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


def run(test, params, env):
    """
    Test for basic controller device function.

    1) Define the VM with specified controller device and check result meets
       expectation.
    2) Start the guest and check if start result meets expectation
    3) Test the function of started controller device
    4) Shutdown the VM and clean up environment
    """

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

    def setup_controller_xml():
        """
        Prepare controller devices of VM XML according to params.
        """
        if cntlr_type is None:
            return

        ctrl = Controller(type_name=cntlr_type)

        if model is not None:
            ctrl.model = model
        if pcihole is not None:
            ctrl.pcihole64 = pcihole
        if vectors is not None:
            ctrl.vectors = vectors
        if index is not None:
            ctrl.index = index
        if addr_str is not None:
            match = re.match(r"(?P<bus>[0-9]*):(?P<slot>[0-9]*).(?P<function>[0-9])", addr_str)
            if match:
                addr_dict = match.groupdict()
                addr_dict['bus'] = hex(int(addr_dict['bus']))
                addr_dict['slot'] = hex(int(addr_dict['slot']))
                addr_dict['function'] = hex(int(addr_dict['function']))
                addr_dict['domain'] = '0x0000'
                ctrl.address = ctrl.new_controller_address(attrs=addr_dict)

        logging.debug("Controller XML is:%s", ctrl)
        vm_xml.add_device(ctrl)

        if usb_cntlr_model is not None:
            ctrl = Controller(type_name='usb')
            ctrl.model = usb_cntlr_model
            if usb_cntlr_addr is not None:
                match = re.match(r"(?P<bus>[0-9]*):(?P<slot>[0-9]*).(?P<function>[0-9])", usb_cntlr_addr)
                if match:
                    addr_dict = match.groupdict()
                    addr_dict['bus'] = hex(int(addr_dict['bus']))
                    addr_dict['slot'] = hex(int(addr_dict['slot']))
                    addr_dict['function'] = hex(int(addr_dict['function']))
                    addr_dict['domain'] = '0x0000'
                    ctrl.address = ctrl.new_controller_address(attrs=addr_dict)
            vm_xml.add_device(ctrl)

    def define_and_check():
        """
        Predict the error message when defining and try to define the guest
        with testing XML.
        """
        fail_patts = []
        known_models = {
            'pci': ['pci-root', 'pcie-root', 'pci-bridge'],
            'virtio-serial': [],
            'usb': ['ehci', 'ich9-ehci1'],
        }

        if cntlr_type == 'pci' and model is None:
            fail_patts.append(r"Invalid PCI controller model")
        if model is not None and model not in known_models[cntlr_type]:
            fail_patts.append(r"Unknown model type")
        if os_machine == 'q35' and model in ['pci-root', 'pci-bridge']:
            fail_patts.append(r"Device requires a PCI Express slot")
        if os_machine == 'i440fx' and model == 'pcie-root':
            fail_patts.append(r"Device requires a standard PCI slot")
        # isdigit will return false on negative number, which just meet the
        # requirement of this test.
        if index is not None and not index.isdigit():
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
        if model == 'pci-bridge' and (index is None or int(index) == 0):
            fail_patts.append(r"PCI bridge index should be > 0")
        res = virsh.start(vm_name)
        libvirt.check_result(res, expected_fails=fail_patts)
        return not res.exit_status

    def get_controller_addr(cntlr_type=None, model=None, index=None):
        """
        Get the address of testing controller from VM XML as a string with
        format "bus:slot.function".
        """
        cur_vm_xml = VMXML.new_from_dumpxml(vm_name)
        addr = None
        for elem in cur_vm_xml.xmltreefile.findall('/devices/controller'):
            if (
                    (cntlr_type is None or elem.get('type') == cntlr_type) and
                    (model is None or elem.get('model') == model) and
                    (index is None or elem.get('index') == index)):
                addr_elem = elem.find('./address')
                if addr_elem is not None:
                    addr = Address.new_from_element(addr_elem).attrs

        if addr is not None:
            bus = int(addr['bus'], 0)
            slot = int(addr['slot'], 0)
            func = int(addr['function'], 0)
            addr_str = '%02d:%02d.%1d' % (bus, slot, func)
            logging.debug("String for address element %s is %s", addr, addr_str)
            return addr_str

    def check_controller_addr():
        """
        Check test controller address against expectation.
        """
        addr_str = get_controller_addr(cntlr_type, model, index)

        if model in ['pci-root', 'pcie-root']:
            if addr_str is None:
                return
            else:
                raise error.TestFail('Expect controller do not have address, '
                                     'but got "%s"' % addr_str)

        exp_addr_patt = r'00:[0-9]{2}.[0-9]'
        if model in ['ehci']:
            exp_addr_patt = r'0[1-9]:[0-9]{2}.[0-9]'
        if addr_str is not None:
            exp_addr_patt = addr_str

        if not re.match(exp_addr_patt, addr_str):
            raise error.TestFail('Expect get controller address "%s", '
                                 'but got "%s"' % (exp_addr_patt, addr_str))

    def check_qemu_cmdline():
        """
        Check domain qemu command line against expectation.
        """
        cmdline = open('/proc/%s/cmdline' % vm.get_pid()).read()
        logging.debug('Qemu command line: %s', cmdline)
        options = cmdline.split('\x00')

        # Get pcihole options from qemu command line
        pcihole_opt = ''
        for idx, opt in enumerate(options):
            if 'pci-hole64-size' in opt:
                pcihole_opt = opt

        # Get expected pcihole options from params
        exp_pcihole_opt = ''
        if cntlr_type == 'pci' and model in ['pci-root', 'pcie-root'] and pcihole is not None:
            if 'i440fx' in os_machine:
                exp_pcihole_opt = 'i440FX-pcihost'
            elif 'q35' in os_machine:
                exp_pcihole_opt = 'q35-pcihost'
            exp_pcihole_opt += '.pci-hole64-size=%sK' % pcihole

        # Check options against expectation
        if pcihole_opt != exp_pcihole_opt:
            raise error.TestFail('Expect get qemu command serial option "%s", '
                                 'but got "%s"' % (exp_pcihole_opt, pcihole_opt))

    def check_msi():
        """
        Check MSI state against expectation.
        """
        addr_str = get_controller_addr(cntlr_type='virtio-serial')

        if addr_str is None:
            raise error.TestError("Can't find target controller in XML")

        session = vm.wait_for_login()
        status, output = session.cmd_status_output('lspci -vvv -s %s' % addr_str)
        logging.debug("lspci output is: %s", output)

        if (vectors is not None and int(vectors) == 0):
            if 'MSI' in output:
                raise error.TestFail('Expect MSI disable with zero vectors,'
                                     ' but got %s' % output)
        if (vectors is None or int(vectors) != 0):
            if 'MSI' not in output:
                raise error.TestFail('Expect MSI enable with non-zero vectors,'
                                     ' but got %s' % output)

    os_machine = params.get('os_machine', 'i440fx')
    cntlr_type = params.get('controller_type', None)
    model = params.get('controller_model', None)
    index = params.get('controller_index', None)
    vectors = params.get('controller_vectors', None)
    pcihole = params.get('controller_pcihole64', None)
    addr_str = params.get('controller_address', None)
    usb_cntlr_model = params.get('usb_controller_model', None)
    usb_cntlr_addr = params.get('usb_controller_address', None)
    vm_name = params.get("main_vm", "virt-tests-vm1")

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()
    try:
        vm_xml.remove_all_device_by_type('controller')
        remove_all_addresses(vm_xml)
        remove_usb_devices(vm_xml)
        setup_controller_xml()
        setup_os_xml()
        logging.debug("Test VM XML is %s" % vm_xml)

        if not define_and_check():
            logging.debug("Can't define the VM, exiting.")
            return

        check_controller_addr()

        try:
            if not start_and_check():
                logging.debug("Can't start the VM, exiting.")
                return
        except virt_vm.VMStartError, detail:
            raise error.TestFail(detail)

        check_qemu_cmdline()

        if cntlr_type == 'virtio-serial':
            check_msi()
    finally:
        vm_xml_backup.sync()
