import re
import logging

from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.controller import Controller


def remove_devices(vm_xml, type):
    """
    Remove all addresses for all devices who has one.

    :param vm_xml: The VM XML to be modified
    :param type: The device type for removing

    :return: True if success, otherwise, False
    """
    if type not in ['address', 'usb']:
        return
    type_dict = {'address': '/devices/*/address',
                 'usb': '/devices/*'}
    try:
        for elem in vm_xml.xmltreefile.findall(type_dict[type]):
            if type == 'usb':
                if elem.get('bus') == 'usb':
                    vm_xml.xmltreefile.remove(elem)
            else:
                vm_xml.xmltreefile.remove(elem)
    except (AttributeError, TypeError) as details:
        logging.error("Fail to remove '%s': %s", type, details)
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
        Prepare os part of VM XML.

        """
        osxml = vm_xml.os
        orig_machine = osxml.machine
        if os_machine:
            osxml.machine = os_machine
            vm_xml.os = osxml
        else:
            cur_machine = orig_machine

    def setup_controller_xml(index, addr_target=None):
        """
        Prepare controller devices of VM XML.

        :param index: The index of controller
        :param addr_target: The controller address

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
        if chassisNr:
            ctrl.target = {'chassisNr': chassisNr}
        if model_name:
            ctrl.model_name = {'name': model_name}

        if addr_target:
            match = re.match(r"(?P<bus>[0-9]*):(?P<slot>[0-9a-f]*).(?P<function>[0-9])", addr_target)
            if match:
                addr_dict = match.groupdict()
                addr_dict['bus'] = hex(int(addr_dict['bus'], 16))
                addr_dict['slot'] = hex(int(addr_dict['slot'], 16))
                addr_dict['function'] = hex(int(addr_dict['function'], 16))
                addr_dict['domain'] = '0x0000'
                ctrl.address = ctrl.new_controller_address(attrs=addr_dict)

        logging.debug("Controller XML is:%s", ctrl)
        vm_xml.add_device(ctrl)

        if cmpnn_cntlr_model is not None:
            for num in range(int(cmpnn_cntlr_num)):
                ctrl = Controller(type_name=cntlr_type)
                ctrl.model = cmpnn_cntlr_model + str(num+1)
                ctrl.index = index
                logging.debug("Controller XML is:%s", ctrl)
                vm_xml.add_device(ctrl)

    def define_and_check():
        """
        Predict the error message when defining and try to define the guest
        with testing XML.
        """
        fail_patts = []
        if expect_err_msg:
            fail_patts.append(expect_err_msg)
        vm_xml.undefine()
        res = vm_xml.virsh.define(vm_xml.xml)
        logging.debug("Expect failures: %s", fail_patts)
        libvirt.check_result(res, expected_fails=fail_patts)
        return not res.exit_status

    def start_and_check():
        """
        Predict the error message when starting and try to start the guest.
        """
        fail_patts = []
        if expect_err_msg:
            fail_patts.append(expect_err_msg)
        res = virsh.start(vm_name)
        logging.debug("Expect failures: %s", fail_patts)
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
        name = elem.alias.get('name')
        if elem.model != 'dmi-to-pci-bridge':
            chassisNR = elem.target.get('chassisNr')
            value = "pci-bridge,chassis_nr=%s" % chassisNR
            value = "%s,id=%s,bus=pci.%d,addr=%#x" % (value, name, bus, slot)
        else:
            value = "%s" % elem.model_name['name']
            value = "%s,id=%s,bus=pcie.%d,addr=%#x" % (value, name, bus, slot)

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

        :return: Tuple (Controller, List)
                       Boolean: True if the controller is found. Otherwise, False.
                       List: a list including qemu search patterns
        """
        logging.debug("Search controller with type %s, model %s index %s",
                      cntl_type, cntl_model, cntl_index)
        qemu_list = None
        found = False
        for elem in vm_xml.devices.by_device_tag('controller'):
            if (elem.type == cntl_type and
               elem.model == cntl_model and
               elem.index == cntl_index):
                found = True
                if (qemu_pattern and
                   cntl_model != 'pci-root' and
                   cntl_model != 'pcie-root'):
                    qemu_list = prepare_qemu_pattern(elem)
                return (elem, qemu_list)
        if not found:
            test.fail("Can not find %s controller "
                      "with index %s." % (cntl_model, cntl_index))

    def get_patt_inx_ctl(cur_vm_xml, qemu_list, inx):
        """
        Get search pattern in qemu line for some kind of cases

        :param cur_vm_xml: Guest xml
        :param qemu_list: List for storing qemu search patterns
        :param inx: Controller index used

        :return: a tuple for (search_result, qemu_list)

        """
        (search_result, qemu_search) = search_controller(cur_vm_xml,
                                                         cntlr_type,
                                                         model,
                                                         inx)
        if qemu_search:
            qemu_list.extend(qemu_search)
        return (search_result, qemu_list)

    def get_patt_non_zero_bus(cur_vm_xml, qemu_list):
        """

        """
        actual_set = set()
        for elem in cur_vm_xml.devices.by_device_tag('controller'):
            if (elem.type == cntlr_type and elem.model == model):
                actual_set.add(int(elem.index))
                qemu_list = prepare_qemu_pattern(elem)
        expect_set = set()
        for num in range(1, int(pci_bus_number) + 1):
            expect_set.add(num)

        logging.debug("expect: %s, actual: %s", expect_set, actual_set)
        if (not actual_set.issubset(expect_set) or
                not expect_set.issubset(actual_set)):
            test.fail("The actual index set (%s)does "
                      "not match the expect index set "
                      "(%s)." % (actual_set, expect_set))
        return qemu_list

    def get_search_patt_qemu_line():
        """
        Check if the guest XML has the expected content.

        :return: -device pci-bridge,chassis_nr=1,id=pci.1,bus=pci.0,addr=0x3
        """
        cur_vm_xml = VMXML.new_from_dumpxml(vm_name)
        qemu_list = []
        # Check the pci-root controller has index = 0
        if no_pci_controller == "yes":
            (_, qemu_list) = get_patt_inx_ctl(cur_vm_xml,
                                              qemu_list, '0')
            return qemu_list

        # Check index numbers of pci-bridge controllers should be equal
        # to the pci_bus_number
        if int(pci_bus_number) > 0:
            return get_patt_non_zero_bus(cur_vm_xml, qemu_list)
        # All controllers should exist if there is a gap between two PCI
        # controller indexes
        if index and index_second and int(index) > 0 and int(index_second) > 0:
            for idx in range(int(index_second), int(index) + 1):
                (_, qemu_list) = get_patt_inx_ctl(cur_vm_xml,
                                                  qemu_list, str(idx))
            return qemu_list

        # All controllers should exist with index among [1..index]
        if index and int(index) > 0 and not index_second:
            for idx in range(1, int(index) + 1):
                (search_result, qemu_list) = get_patt_inx_ctl(cur_vm_xml,
                                                              qemu_list,
                                                              str(idx))
                if not search_result:
                    test.fail("Can not find %s controller "
                              "with index %s." % (model, str(idx)))
            return qemu_list

    def get_controller_addr(cntlr_type=None, model=None, index=None, cntlr_bus=None):
        """
        Get the address of testing controller from VM XML as a string with
        format
        a. "bus:slot.function" for pci address type
        b. "cssid:ssid.devno" for ccw address type

        :param cntlr_type: controller type
        :param model: controller model
        :param index: controller index
        :param cntlr_bus: controller bus type, e.g. pci, ccw
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

                if 'ccw' == cntlr_bus:
                    p1 = int(addr_elem.attrs.get('cssid'), 0)
                    p2 = int(addr_elem.attrs.get('ssid'), 0)
                    p3 = int(addr_elem.attrs.get('devno'), 0)
                else:
                    p1 = int(addr_elem.attrs.get('bus'), 0)
                    p2 = int(addr_elem.attrs.get('slot'), 0)
                    p3 = int(addr_elem.attrs.get('function'), 0)
                addr_str = '%02d:%02x.%1d' % (p1, p2, p3)
                logging.debug("Controller address is %s", addr_str)
                break

        return addr_str

    def check_controller_addr(cntlr_bus=None):
        """
        Check test controller address against expectation.
        :param cntlr_bus: controller bus type, e.g. pci, ccw
        """
        addr_str = get_controller_addr(cntlr_type, model, index, cntlr_bus)

        if model in ['pci-root', 'pcie-root']:
            if addr_str is None:
                return
            else:
                test.fail('Expect controller do not have address, '
                          'but got "%s"' % addr_str)

        if index != 0:
            if '00:00' in addr_str:
                test.fail("Invalid PCI address 0000:00:00, "
                          "at least one of domain, bus, "
                          "or slot must be > 0")

        exp_addr_patt = r'00:[0-9]{2}.[0-9]'
        if model in ['ehci']:
            exp_addr_patt = r'0[1-9]:[0-9]{2}.[0-9]'
        if addr_str:
            exp_addr_patt = addr_str
        if 'ccw' == cntlr_bus:
            exp_addr_patt = r'254:\d+\.\d+'

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

        # Check usb options against expectation
        if cntlr_type == "usb":
            pattern = ""
            if cmpnn_cntlr_num is not None:
                for num in range(int(cmpnn_cntlr_num)):
                    name = (cmpnn_cntlr_model+str(num+1)).split('-')
                    pattern = pattern + r"-device.%s-usb-%s.*" % (name[0], name[1])
            elif model == "ehci":
                pattern = r"-device.usb-ehci"
            elif model == "qemu-xhci":
                pattern = r"-device.qemu-xhci"

            logging.debug("pattern is %s", pattern)

            if pattern and not re.search(pattern, cmdline):
                test.fail("Expect get usb model info in qemu cmdline, but failed!")

    def check_guest(cntlr_type, cntlr_model, cntlr_index=None, cntlr_bus=""):
        """
        Check status within the guest against expectation.
        :param cntlr_type: //controller@type, e.g. ide
        :param cntlr_model: //controller@model, e.g. virtio-scsi
        :param cntlr_index: //controller@index, e.g. '0'
        :param cntlr_bus: //controller/address@type, e.g. pci
        :raise avocado.core.exceptions.TestFail: Fails the test if checks fail
        :raise avocado.core.exceptions.TestError: Fails if test couldn't be fully executed
        :return: None
        """
        if model == 'pci-root' or model == 'pcie-root':
            return

        addr_str = get_controller_addr(cntlr_type=cntlr_type,
                                       model=cntlr_model,
                                       index=cntlr_index,
                                       cntlr_bus=cntlr_bus)

        if 'ccw' == cntlr_bus:
            check_ccw_bus_type(addr_str)
        else:
            check_pci_bus_type(addr_str, cntlr_index, cntlr_model, cntlr_type)

    def check_ccw_bus_type(addr_str):
        """
        Uses lszdev to check for device info in guest.
        :param addr_str: Device address from libvirt
        :raise avocado.core.exceptions.TestFail: Fails the test if unexpected test values
        :raise avocado.core.exceptions.TestError: Fails if can't query dev info in guest
        :return: None
        """
        session = vm.wait_for_login(serial=True)
        cmd = 'lszdev generic-ccw --columns ID'
        status, output = session.cmd_status_output(cmd)
        logging.debug("lszdev output is: %s", output)
        if status:
            test.error("Failed to get guest device info, check logs.")
        devno = int(addr_str.split('.')[-1])
        devno_str = hex(devno).replace('0x', '').zfill(4)
        if devno_str not in output:
            test.fail("Can't find device with number %s in guest. Searched for %s in %s"
                      % (devno, devno_str, output))

    def check_pci_bus_type(addr_str, cntlr_index, cntlr_model, cntlr_type):
        """
        Uses lspci to check for device info in guest.
        :param addr_str: Device address from libvirt
        :param cntlr_index: controller index
        :param cntlr_model: controller model
        :param cntlr_type: controller type
        :raise avocado.core.exceptions.TestError: Fails if device info not found
        :raise avocado.core.exceptions.TestFail: Fails if unexcepted test values
        :return: None
        """
        pci_name = 'PCI bridge:'
        verbose_option = ""
        if cntlr_type == 'virtio-serial':
            verbose_option = '-vvv'
        if (addr_str is None and model != 'pci-root' and model != 'pcie-root'):
            test.error("Can't find target controller in XML")
        if cntlr_index:
            logging.debug("%s, %s, %s", cntlr_type, cntlr_model, cntlr_index)
        if (addr_str is None and cntlr_model != 'pci-root' and cntlr_model != 'pcie-root'):
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

    os_machine = params.get('machine_type', None)
    libvirt.check_machine_type_arch(os_machine)
    cntlr_type = params.get('controller_type', None)
    model = params.get('controller_model', None)
    index = params.get('controller_index', None)
    vectors = params.get('controller_vectors', None)
    pcihole = params.get('controller_pcihole64', None)
    chassisNr = params.get('chassisNr', None)
    addr_str = params.get('controller_address', None)
    cmpnn_cntlr_model = params.get('companion_controller_model', None)
    cmpnn_cntlr_num = params.get('companion_controller_num', None)
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    no_pci_controller = params.get("no_pci_controller", "no")
    pci_bus_number = params.get("pci_bus_number", "0")
    remove_address = params.get("remove_address", "yes")
    setup_controller = params.get("setup_controller", "yes")
    index_second = params.get("controller_index_second", None)
    cntlr_bus = params.get('controller_bus')
    cur_machine = os_machine
    check_qemu = "yes" == params.get("check_qemu", "no")
    check_within_guest = "yes" == params.get("check_within_guest", "no")
    run_vm = "yes" == params.get("run_vm", "no")
    second_level_controller_num = params.get("second_level_controller_num", "0")
    status_error = "yes" == params.get("status_error", "no")
    model_name = params.get("model_name", None)
    expect_err_msg = params.get("err_msg", None)

    if index and index_second:
        if int(index) > int(index_second):
            test.error("Invalid parameters")

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    try:
        vm_xml.remove_all_device_by_type('controller')
        if remove_address == "yes":
            remove_devices(vm_xml, 'address')
        remove_devices(vm_xml, 'usb')
        if setup_controller == "yes":
            if index_second:
                setup_controller_xml(index_second)
            setup_controller_xml(index, addr_str)
            if second_level_controller_num:
                for indx in range(2, int(second_level_controller_num) + 2):
                    addr_second = "0%s:0%s.0" % (index, str(indx))
                    setup_controller_xml(str(indx), addr_second)
        setup_os_xml()
        if int(pci_bus_number) > 0:
            address_params = {'bus': "%0#4x" % int(pci_bus_number)}
            libvirt.set_disk_attr(vm_xml, 'vda', 'address', address_params)

        logging.debug("Test VM XML before define is %s" % vm_xml)

        if not define_and_check():
            logging.debug("Can't define the VM, exiting.")
            return
        vm_xml = VMXML.new_from_dumpxml(vm_name)
        logging.debug("Test VM XML after define is %s" % vm_xml)

        check_controller_addr(cntlr_bus)
        if run_vm:
            try:
                if not start_and_check():
                    logging.debug("Can't start the VM, exiting.")
                    return
            except virt_vm.VMStartError as detail:
                test.fail(detail)

            search_qemu_cmd = get_search_patt_qemu_line()
            if check_qemu:
                check_qemu_cmdline(search_pattern=search_qemu_cmd)

            if check_within_guest:
                try:
                    if int(pci_bus_number) > 0:
                        for contr_idx in range(1, int(pci_bus_number) + 1):
                            check_guest(cntlr_type, model, str(contr_idx))
                        return
                    if index:
                        check_max_index = int(index) + int(second_level_controller_num)
                        for contr_idx in range(1, int(check_max_index) + 1):
                            check_guest(cntlr_type, model, str(contr_idx))
                    else:
                        check_guest(cntlr_type, model, cntlr_bus=cntlr_bus)
                        if model == 'pcie-root':
                            # Need check other auto added controller
                            check_guest(cntlr_type, 'dmi-to-pci-bridge', '1')
                            check_guest(cntlr_type, 'pci-bridge', '2')
                except remote.LoginTimeoutError as e:
                    logging.debug(e)
                    if not status_error:
                        raise

    finally:
        vm_xml_backup.sync()
