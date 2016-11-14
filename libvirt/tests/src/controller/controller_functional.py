import re
import logging

from virttest import virt_vm
from virttest import virsh
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.controller import Controller


def remove_all_addresses(vm_xml):
    """
    Remove all addresses for all devices who has one.

    :param vm_xml: The VM XML to be modified

    :return: True if success, otherwise, False
    """
    try:
        for elem in vm_xml.xmltreefile.findall('/devices/*/address'):
            vm_xml.xmltreefile.remove(elem)
    except (AttributeError, TypeError) as details:
        logging.error("Fail to remove all addresses: %s", details)
        return False
    vm_xml.xmltreefile.write()
    return True


def remove_usb_devices(vm_xml):
    """
    Remove all USB devices.

    :param vm_xml: The VM XML to be modified

    :return: True if success, otherwise, False
    """
    try:
        for xml in vm_xml.xmltreefile.findall('/devices/*'):
            if xml.get('bus') == 'usb':
                vm_xml.xmltreefile.remove(xml)
    except (AttributeError, TypeError) as details:
        logging.error("Fail to remove usb devices: %s", details)
        return False
    vm_xml.xmltreefile.write()
    return True


def remove_iface_devices(vm_xml):
    """
    Remove all the interface devices

    :param vm_xml: The VM XML to be modified

    :return: True if success, otherwise False
    """
    logging.debug("remove_iface_devices")
    try:
        for xml in vm_xml.xmltreefile.findall('/devices/interface'):
            vm_xml.xmltreefile.remove(xml)
    except (AttributeError, TypeError) as details:
        logging.error("Fail to remove the devices: %s", details)
        return False
    vm_xml.xmltreefile.write()
    return True


def run(test, params, env):
    """
    Test for basic controller device function.

    1) Define the VM w/o specified controller device and check result meets
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
        if os_machine:
            osxml.machine = os_machine
            vm_xml.os = osxml
        else:
            cur_machine = orig_machine

    def setup_controller_xml(index):
        """
        Prepare controller devices of VM XML according to params.
        """
        ctrl = Controller(type_name=cntlr_type)

        if model:
            ctrl.model = model
        if pcihole:
            ctrl.pcihole64 = pcihole
        if vectors:
            ctrl.vectors = vectors
        if index:
            ctrl.index = index
        if addr_str:
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

        if usb_cntlr_model:
            ctrl = Controller(type_name='usb')
            ctrl.model = usb_cntlr_model
            if usb_cntlr_addr:
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
        if model and model not in known_models[cntlr_type]:
            fail_patts.append(r"Unknown model type")
        if os_machine and os_machine == 'q35' and model in ['pci-root', 'pci-bridge']:
            fail_patts.append(r"Device requires a PCI Express slot")
        if os_machine == 'pc' and model == 'pcie-root':
            fail_patts.append(r"Device requires a standard PCI slot")
        # isdigit will return false on negative number, which just meet the
        # requirement of this test.
        if index and not index.isdigit():
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
        if index and int(index) <= 0 and model == 'pci-bridge':
            fail_patts.append(r"PCI bridge index should be > 0")
        res = virsh.start(vm_name)
        libvirt.check_result(res, expected_fails=fail_patts)
        return not res.exit_status

    def prepare_qemu_pattern(elem):
        """
        Collect the patterns to be searched in qemu command line.

        :param elem: a Controller object

        :return: A list including search patterns
        """
        search_qemu_cmd = []

        bus = int(elem.address.attrs.get('bus'), 0)
        slot = int(elem.address.attrs.get('slot'), 0)
        func = int(elem.address.attrs.get('function'), 0)
        addr_str = '%02d:%02d.%1d' % (bus, slot, func)
        chassisNR = elem.target.get('chassisNr')
        name = elem.alias.get('name')
        value = "pci-bridge,chassis_nr=%s" % chassisNR
        value = "%s,id=%s,bus=pci.0,addr=%#x" % (value, name, slot)
        tup = ('-device', value)
        search_qemu_cmd.append(tup)
        return search_qemu_cmd

    def search_controller(vm_xml, cntl_type, cntl_model, cntl_index,
                          qemu_pattern=True):
        """
        Search a controller as specified and prepare the expected qemu
        command line
        :params vm_xml: The guest VMXML instance
        :params cntl_type: The controller type
        :params cntl_model: The controller model
        :params cntl_index: The controller index
        :params qemu_pattern: True if it needs to be checked with qemu
                              command line. False if not.

        :return: Tuple (Boolean, List)
                       Boolean: True if the controller is found. Otherwise, False.
                       List: a list including qemu search patterns
        """
        logging.debug("Search controller with type %s, model %s index %s",
                      cntl_type, cntl_model, cntl_index)
        qemu_list = None
        for elem in vm_xml.devices.by_device_tag('controller'):
            logging.debug(elem)
            if (elem.type == cntl_type and
               elem.model == cntl_model and
               elem.index == cntl_index):
                if (qemu_pattern and
                   cntl_model != 'pci-root' and
                   cntl_model != 'pcie-root'):
                    qemu_list = prepare_qemu_pattern(elem)
                return (True, qemu_list)

        return (False, qemu_list)

    def check_guest_xml():
        """
        Check if the guest XML has the expected content.

        :return: -device pci-bridge,chassis_nr=1,id=pci.1,bus=pci.0,addr=0x3
        """
        cur_vm_xml = VMXML.new_from_dumpxml(vm_name)
        logging.debug("Current guest XML:%s\n", cur_vm_xml)
        qemu_list = []
        # Check the pci-root controller has index = 0
        if no_pci_controller == "yes":
            (search_result, qemu_list) = search_controller(cur_vm_xml,
                                                           cntlr_type,
                                                           model,
                                                           '0')
            if not search_result:
                test.fail("Can not find %s controller "
                          "with index 0." % model)
        # Check index numbers of pci-bridge controllers should be equal
        # to the pci_bus_number
        if int(pci_bus_number) > 0:
            actual_set = set()
            for elem in cur_vm_xml.devices.by_device_tag('controller'):
                if (elem.type == cntlr_type and elem.model == model):
                    actual_set.add(int(elem.index))
                    qemu_list = prepare_qemu_pattern(elem)
            expect_set = set()
            for num in range(1, int(pci_bus_number)+1):
                expect_set.add(num)

            logging.debug("expect: %s, actual: %s", expect_set, actual_set)
            if (not actual_set.issubset(expect_set) or
               not expect_set.issubset(actual_set)):
                test.fail("The actual index set (%s)does "
                          "not match the expect index set "
                          "(%s)." % (actual_set, expect_set))
        # All controllers should exist with index among [1..index]
        if index and int(index) > 0:
            for idx in range(1, int(index) + 1):
                (search_result, qemu_search) = search_controller(cur_vm_xml,
                                                                 cntlr_type,
                                                                 model,
                                                                 str(idx))
                if not search_result:
                    test.fail("Can not find '%s' controller "
                              "with index %s." % (model,
                                                  str(idx)))
                if qemu_search:
                    qemu_list.extend(qemu_search)
        # All controllers should exist if there is a gap between two PCI
        # controller indexes
        if index and index_second:
            for idx in range(1, int(index_second) + 1):
                (search_result, qemu_search) = search_controller(cur_vm_xml,
                                                                 cntlr_type,
                                                                 model,
                                                                 str(idx))
                if not search_result:
                    test.fail("Can not find %s controller "
                              "with index %s." % (model, str(idx)))
                if qemu_search:
                    qemu_list.extend(qemu_search)

        return qemu_list

    def get_controller_addr(cntlr_type=None, model=None, index=None):
        """
        Get the address of testing controller from VM XML as a string with
        format "bus:slot.function".

        :param cntlr_type: controller type
        :param model: controller model
        :param index: controller index

        :return: an address string of the specified controller
        """
        if model in ['pci-root', 'pcie-root']:
            return None

        addr_str = None
        cur_vm_xml = VMXML.new_from_dumpxml(vm_name)

        for elem in cur_vm_xml.devices.by_device_tag('controller'):
            if (
                    (cntlr_type is None or elem.type == cntlr_type) and
                    (model is None or elem.model == model) and
                    (index is None or elem.index == index)):
                addr_elem = elem.address
                if addr_elem is None:
                    test.error("Can not find 'Address' "
                               "element for the controller")

                bus = int(addr_elem.attrs.get('bus'), 0)
                slot = int(addr_elem.attrs.get('slot'), 0)
                func = int(addr_elem.attrs.get('function'), 0)
                addr_str = '%02d:%02x.%1d' % (bus, slot, func)
                logging.debug("Controller address is %s", addr_str)

        return addr_str

    def check_controller_addr(test):
        """
        Check test controller address against expectation.
        """
        addr_str = get_controller_addr(cntlr_type, model, index)

        if model in ['pci-root', 'pcie-root']:
            if addr_str is None:
                return
            else:
                test.fail('Expect controller do not have address, '
                          'but got "%s"' % addr_str)

        exp_addr_patt = r'00:[0-9]{2}.[0-9]'
        if model in ['ehci']:
            exp_addr_patt = r'0[1-9]:[0-9]{2}.[0-9]'
        if addr_str:
            exp_addr_patt = addr_str

        if not re.match(exp_addr_patt, addr_str):
            test.fail('Expect get controller address "%s", '
                      'but got "%s"' % (exp_addr_patt, addr_str))

    def check_qemu_cmdline(search_pattern=None):
        """
        Check domain qemu command line against expectation.

        :param search_pattern: search list with tuple objects
        """
        with open('/proc/%s/cmdline' % vm.get_pid()) as proc_file:
            cmdline = proc_file.read()
        logging.debug('Qemu command line: %s', cmdline)

        options = cmdline.split('\x00')
        # Search the command line options for the given patterns
        if search_pattern and isinstance(search_pattern, list):
            for pattern in search_pattern:
                key = pattern[0]
                value = pattern[1]
                logging.debug("key=%s, value=%s", key, value)
                found = False
                check_value = False
                for opt in options:
                    if check_value:
                        if opt == value:
                            logging.debug("Found the expected (%s %s) in qemu "
                                          "command line" % (key, value))
                            found = True
                            break
                        check_value = False
                    if key == opt:
                        check_value = True
                if not found:
                    test.fail("Can not find '%s %s' in qemu "
                              "command line" % (key, value))

        # Get pcihole options from qemu command line
        pcihole_opt = ''
        for idx, opt in enumerate(options):
            if 'pci-hole64-size' in opt:
                pcihole_opt = opt

        # Get expected pcihole options from params
        exp_pcihole_opt = ''
        if (cntlr_type == 'pci' and model in ['pci-root', 'pcie-root'] and
           pcihole):
            if 'pc' in cur_machine:
                exp_pcihole_opt = 'i440FX-pcihost'
            elif 'q35' in cur_machine:
                exp_pcihole_opt = 'q35-pcihost'
            exp_pcihole_opt += '.pci-hole64-size=%sK' % pcihole

        # Check options against expectation
        if pcihole_opt != exp_pcihole_opt:
            test.fail('Expect get qemu command serial option "%s", '
                      'but got "%s"' % (exp_pcihole_opt, pcihole_opt))

    def check_guest(cntlr_type, cntlr_model, cntlr_index=None):
        """
        Check status within the guest against expectation.
        """
        if model == 'pci-root' or model == 'pcie-root':
            return
        addr_str = get_controller_addr(cntlr_type=cntlr_type,
                                       model=cntlr_model,
                                       index=cntlr_index)
        pci_name = 'PCI bridge:'
        verbose_option = ""
        if cntlr_type == 'virtio-serial':
            verbose_option = '-vvv'

        if (addr_str is None and model != 'pci-root' and model != 'pcie-root'):
            test.error("Can't find target controller in XML")

        session = vm.wait_for_login(serial=True)
        status, output = session.cmd_status_output('lspci %s -s %s'
                                                   % (verbose_option, addr_str))
        logging.debug("lspci output is: %s", output)

        if (cntlr_type == 'virtio-serial' and
           (vectors and int(vectors) == 0)):
            if 'MSI' in output:
                test.fail("Expect MSI disable with zero vectors, "
                          "but got %s" % output)
        if (cntlr_type == 'virtio-serial' and
           (vectors is None or int(vectors) != 0)):
            if 'MSI' not in output:
                test.fail("Expect MSI enable with non-zero vectors, "
                          "but got %s" % output)
        if (cntlr_type == 'pci'):
            if pci_name not in output:
                test.fail("Can't find target pci device"
                          " '%s' on guest " % addr_str)

    os_machine = params.get('os_machine', None)
    cntlr_type = params.get('controller_type', None)
    model = params.get('controller_model', None)
    index = params.get('controller_index', None)
    vectors = params.get('controller_vectors', None)
    pcihole = params.get('controller_pcihole64', None)
    addr_str = params.get('controller_address', None)
    usb_cntlr_model = params.get('usb_controller_model', None)
    usb_cntlr_addr = params.get('usb_controller_address', None)
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    no_pci_controller = params.get("no_pci_controller", "no")
    pci_bus_number = params.get("pci_bus_number", "0")
    remove_address = params.get("remove_address", "yes")
    setup_controller = params.get("setup_controller", "yes")
    index_second = params.get("controller_index_second", None)
    cur_machine = os_machine

    if index and index_second:
        if int(index) >= int(index_second):
            test.error("index_second should be larger than index.")

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    try:
        vm_xml.remove_all_device_by_type('controller')
        if remove_address == "yes":
            remove_all_addresses(vm_xml)
        remove_usb_devices(vm_xml)
        if setup_controller == "yes":
            setup_controller_xml(index)
        if index_second:
            setup_controller_xml(index_second)
        setup_os_xml()
        if int(pci_bus_number) > 0:
            address_params = {'bus': "%0#4x" % int(pci_bus_number)}
            libvirt.set_disk_attr(vm_xml, 'vda', 'address', address_params)

        logging.debug("Test VM XML is %s" % vm_xml)

        if not define_and_check():
            logging.debug("Can't define the VM, exiting.")
            return

        check_controller_addr(test)

        try:
            if not start_and_check():
                logging.debug("Can't start the VM, exiting.")
                return
        except virt_vm.VMStartError as detail:
            test.fail(detail)

        search_qemu_cmd = check_guest_xml()
        check_qemu_cmdline(search_pattern=search_qemu_cmd)

        if int(pci_bus_number) == 0:
            check_guest(cntlr_type, model)
        else:
            for contr_idx in range(1, int(pci_bus_number) + 1):
                check_guest(cntlr_type, model, str(contr_idx))

    finally:
        vm_xml_backup.sync()
