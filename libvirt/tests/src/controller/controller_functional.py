import re
import logging
import tempfile
import platform

from virttest import virt_vm
from virttest import virsh
from virttest import remote
from virttest import data_dir
from virttest import utils_misc

from virttest.utils_test import libvirt
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.vm_xml import VMCPUXML
from virttest.libvirt_xml.devices.controller import Controller


def remove_devices(vm_xml, type):
    """
    Remove all addresses for all devices who has one.

    :param vm_xml: The VM XML to be modified
    :param type: The device type for removing

    :return: True if success, otherwise, False
    """
    if type not in ['address', 'usb', 'interface']:
        return
    type_dict = {'address': '/devices/*/address',
                 'usb': '/devices/*',
                 'interface': '/devices/interface'}
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
        # avocado-vt only use virt machine type on aarch64
        if platform.machine() == 'aarch64':
            osxml.machine = 'virt'
            return

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

    def define_and_check(guest_xml):
        """
        Define the guest and check the result.

        :param guest_xml: The guest VMXML instance
        """
        fail_patts = []
        if expect_err_msg:
            fail_patts.append(expect_err_msg)
        guest_xml.undefine()
        res = vm_xml.virsh.define(guest_xml.xml)
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

    def get_patt_inx_ctl(cur_vm_xml, qemu_list, inx):
        """
        Get search pattern in qemu line for some kind of cases

        :param cur_vm_xml: Guest xml
        :param qemu_list: List for storing qemu search patterns
        :param inx: Controller index used

        :return: a tuple for (search_result, qemu_list)

        """
        (search_result, qemu_search) = check_cntrl(cur_vm_xml,
                                                   cntlr_type,
                                                   model,
                                                   inx, None, True)
        if qemu_search:
            qemu_list.extend(qemu_search)
        return (search_result, qemu_list)

    def get_patt_non_zero_bus(cur_vm_xml):
        """
        Get search pattern for multiple controllers with non-zero bus.

        :param cur_vm_xml: The guest VMXML instance
        :return: List, The search pattern list
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
            return get_patt_non_zero_bus(cur_vm_xml)
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

        :param cntlr_type: controller type, e.g. pci
        :param model: controller model, e.g. pcie-root-port
        :param index: controller index, e.g. '0'
        :param cntlr_bus: controller bus type, e.g. pci, ccw
        :return: a tuple including an address string, bus, slot,
                        function, multifunction
        """
        if model in ['pci-root', 'pcie-root']:
            return (None, None, None, None, None)

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
                p4 = None
                if 'ccw' == cntlr_bus:
                    p1 = int(addr_elem.attrs.get('cssid'), 0)
                    p2 = int(addr_elem.attrs.get('ssid'), 0)
                    p3 = int(addr_elem.attrs.get('devno'), 0)
                else:
                    p1 = int(addr_elem.attrs.get('bus'), 0)
                    p2 = int(addr_elem.attrs.get('slot'), 0)
                    p3 = int(addr_elem.attrs.get('function'), 0)
                    p4 = addr_elem.attrs.get('multifunction')
                addr_str = '%02d:%02x.%1d' % (p1, p2, p3)
                logging.debug("Controller address is %s", addr_str)
                return (addr_str, p1, p2, p3, p4)

        return (None, None, None, None, None)

    def check_controller_addr(cntlr_bus=None):
        """
        Check test controller address against expectation.

        :param cntlr_bus: controller bus type, e.g. pci, ccw
        """
        (addr_str, _, _, _, _) = get_controller_addr(cntlr_type, model, index, cntlr_bus)
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
        options = cmdline.split('\x00')
        logging.debug(options)
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
                        if re.findall(value, opt):
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

        (addr_str, _, _, _, _) = get_controller_addr(cntlr_type=cntlr_type,
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

    def check_guest_by_pattern(patterns):
        """
        Search the command output with specified patterns

        :param patterns: patterns to search in guest. Type: str or list
        """
        logging.debug("Search pattern:{}".format(patterns))
        session = vm.wait_for_login(serial=True)
        libvirt.check_cmd_output('lspci', eval(patterns), session=session)
        session.close()

    def check_cntrl(vm_xml, cntlr_type, cntlr_model, cntlr_index,
                    check_dict, qemu_pattern):
        """
        Check the controller or get the controller's search patterns.
        Currently check_dict and qemu_pattern are not
        supported to be used at same time.

        :param vm_xml, the guest VMXML instance
        :param cntlr_type, the controller type
        :param cntlr_model, the controller's model
        :param cntlr_index, the controller's index
        :param check_dict, the dict for checking in the controller
        :param qemu_pattern: True if it needs to be checked with qemu
                              command line. False if not.
        :return Tuple (Controller, List) if qemu_pattern
                       Controller: the controller found.
                       List: a list including qemu search patterns
        :return None if check_dict
        :raise test.fail if the model name is not expected
        :raise test.error if the controller is not found
        """
        qemu_list = None
        for elem in vm_xml.devices.by_device_tag('controller'):
            if (cntlr_type == elem.type and cntlr_model == elem.model):
                if cntlr_index and cntlr_index != elem.index:
                    continue
                if qemu_pattern:
                    if cntlr_model not in ['pci-root', 'pcie-root']:
                        qemu_list = prepare_qemu_pattern(elem)
                    return (elem, qemu_list)
                if check_dict:
                    logging.debug("Checking list {}".format(check_dict))
                    if ('modelname' in check_dict and
                            elem.model_name['name'] != check_dict['modelname']):
                        test.fail("Can't find the expected model name {} "
                                  "with (type:{}, model:{}, index:{}), "
                                  "found {}".format(check_dict['modelname'],
                                                    cntlr_type,
                                                    cntlr_model,
                                                    cntlr_index,
                                                    elem.model_name['name']))
                    if ('busNr' in check_dict and
                            elem.target['busNr'] != check_dict['busNr']):
                        test.fail("Can't find the expected busNr {} "
                                  "with (type:{}, model:{}, index:{}), "
                                  "found {}".format(check_dict['busNr'],
                                                    cntlr_type,
                                                    cntlr_model,
                                                    cntlr_index,
                                                    elem.target['busNr']))
                    else:
                        logging.debug("Check controller successfully")
                        return
        test.error("Can't find the specified controller with "
                   "(type:{}, model:{}, index:{})".format(cntlr_type,
                                                          cntlr_model,
                                                          cntlr_index))

    def detach_device(vm_name):
        """
        Detach a device from the given guest

        :param vm_name: The guest name
        :return: None
        """
        attach_dev_type = params.get("attach_dev_type", 'disk')
        detach_option = params.get("detach_option")
        if attach_dev_type == 'interface':
            ret = virsh.detach_interface(vm_name, detach_option,
                                         **virsh_dargs)
        else:
            logging.debug("No need to detach any device.")

    def attach_device(vm_name):
        """
        Attach devices to the guest for some times

        :param vm_name: The guest name
        :return: None
        """
        attach_count = params.get("attach_count", '1')
        attach_dev_type = params.get("attach_dev_type", 'disk')
        attach_option = params.get("attach_option")
        if attach_option.count('--address '):
            index_str = "%02x" % int(auto_indexes_dict['pcie-root-port'][0])
            attach_option = attach_option % index_str
        for count in range(0, int(attach_count)):
            if attach_dev_type == 'disk':
                file_path = tempfile.mktemp(dir=data_dir.get_tmp_dir())
                libvirt.create_local_disk('file', file_path, size='1')
                ret = virsh.attach_disk(vm_name,
                                        file_path,
                                        params.get('dev_target', 'vdb'),
                                        extra=attach_option,
                                        **virsh_dargs)
            elif attach_dev_type == 'interface':
                ret = virsh.attach_interface(vm_name,
                                             attach_option,
                                             **virsh_dargs)
            else:
                logging.debug("No need to attach any device.")
                break

    def check_detach_attach_result(vm_name, cmd, pattern, expect_output, option='--hmp'):
        """
        Check the attach/detach result by qemu_monitor_command.

        :param vm_name: guest name
        :param cmd: the command for qemu_monitor_command
        :param pattern: regular expr used to search
                        the output of qemu_monitor_command
        :param expect_output: the expected output for qemu_monitor_command
        :param option: option for qemu_monitor_command
        :raise test.fail if the pattern is not matched
        :return: the qemu_monitor_command output
        """
        ret = virsh.qemu_monitor_command(vm_name, cmd, option)
        libvirt.check_result(ret)
        if pattern and expect_output:
            if not re.findall(pattern, ret.stdout.strip()):
                test.fail("Can't find the pattern '{}' in "
                          "qemu monitor command "
                          "output'{}'".format(pattern,
                                              ret.stdout.strip()))
        else:
            return expect_output == ret.stdout.strip()

    def check_guest_by_cmd(cmds, expect_error=False):
        """
        Execute the command within guest and check status

        :param cmds: Str or List, The command executed in guest
        :param expect_error: True if the command is expected to fail
        :return: None
        :raise test.fail if command status is not as expected
        """
        def _check_cmd_result(cmd):
            logging.debug("Command in guest gets result: %s", output)
            if status and not expect_error:
                test.fail("Command '{}' fails in guest with status "
                          "'{}'".format(cmd, status))
            elif status and expect_error:
                logging.debug("Command '{}' fails in guest as "
                              "expected".format(cmd))
            elif not status and not expect_error:
                logging.debug("Check guest by command successfully")
            else:
                test.fail("Check guest by command successfully, "
                          "but expect failure")

        logging.debug("Execute command '{}' in guest".format(cmds))
        session = vm.wait_for_login(serial=True)
        (status, output) = (None, None)
        if isinstance(cmds, str):
            status, output = session.cmd_status_output(cmds)
            _check_cmd_result(cmds)
        elif isinstance(cmds, list):
            for cmd in cmds:
                if isinstance(cmd, str):
                    status, output = session.cmd_status_output(cmd)
                    _check_cmd_result(cmd)
                elif isinstance(cmd, dict):
                    for cmd_key in cmd.keys():
                        status, output = session.cmd_status_output(cmd_key)
                        if output.strip() != cmd[cmd_key]:
                            test.fail("Command '{}' does not get "
                                      "expect result {}, but found "
                                      "{}".format(cmd_key,
                                                  cmd[cmd_key],
                                                  output.strip()))

    def get_device_bus(vm_xml, device_type):
        """
        Get the bus that the devices are attached to.

        :param vm_xml: Guest xml
        :param device_type: The type of device, like disk, interface
        :return a list includes buses the devices attached to
        """
        devices = vm_xml.get_devices(device_type=device_type)
        bus_list = []
        for device in devices:
            logging.debug("device:{}".format(device))
            bus = device.address.attrs['bus']
            logging.debug("This device's bus:{}".format(bus))
            bus_list.append(bus)
        return bus_list

    def add_device_xml(vm_xml, device_type, device_cfg_dict):
        """
        Add a device xml to the existing vm xml

        :param vm_xml: the existing vm xml object
        :param device_type: type of device to be added
        :param device_cfg_dict: the configuration of the device
        :return: None
        """
        vm_xml.remove_all_device_by_type(device_type)
        dev_obj = vm_xml.get_device_class(device_type)()

        dev_cfg = eval(device_cfg_dict)
        if device_type == 'sound':
            dev_obj.model_type = dev_cfg.get("model")
        elif device_type == 'memballoon':
            dev_obj.model = dev_cfg.get("model")
        if 'bus' in dev_cfg:
            dev_obj.address = {'bus': dev_cfg.get("bus"),
                               'type': dev_cfg.get("type", "pci"),
                               'slot': dev_cfg.get("slot", "0x00")}
        vm_xml.add_device(dev_obj)

    def check_multifunction():
        """
        Check if multifunction is found in vm xml for specified controller

        :raise: test.fail if multifunction is not as expected
        """
        (_, _, _, _, multi_func) = get_controller_addr(cntlr_type, model, '0')
        if not multi_func or multi_func != 'on':
            test.fail("Can't find multifunction=on in certain "
                      "controller(type:{}, model:{}, "
                      "index:{})".format(cntlr_type, model, 0))

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
    remove_address = "yes" == params.get("remove_address", "yes")
    remove_contr = "yes" == params.get("remove_contr", "yes")
    setup_controller = params.get("setup_controller", "yes")
    index_second = params.get("controller_index_second", None)
    cntlr_bus = params.get('controller_bus')
    cur_machine = os_machine
    check_qemu = "yes" == params.get("check_qemu", "no")
    check_within_guest = "yes" == params.get("check_within_guest", "no")
    run_vm = "yes" == params.get("run_vm", "no")
    second_level_controller_num = params.get("second_level_controller_num", "0")
    check_contr_addr = "yes" == params.get("check_contr_addr", "yes")
    qemu_patterns = params.get("qemu_patterns")
    status_error = "yes" == params.get("status_error", "no")
    model_name = params.get("model_name", None)
    expect_err_msg = params.get("err_msg", None)
    new_pcie_root_port_model = params.get("new_model")
    old_pcie_root_port_model = params.get("old_model")
    add_contrl_list = params.get("add_contrl_list")
    auto_bus = "yes" == params.get("auto_bus", "no")
    check_cntrls_list = params.get("check_cntrls_list")
    sound_dict = params.get("sound_dict")
    balloon_dict = params.get("balloon_dict")
    guest_patterns = params.get("guest_patterns")
    attach_option = params.get("attach_option")
    detach_option = params.get("detach_option")
    attach_dev_type = params.get("attach_dev_type", 'disk')
    remove_nic = "yes" == params.get("remove_nic", 'no')
    qemu_monitor_cmd = params.get("qemu_monitor_cmd")
    cmd_in_guest = params.get("cmd_in_guest")
    check_dev_bus = "yes" == params.get("check_dev_bus", "no")
    cpu_numa_cells = params.get("cpu_numa_cells")
    virsh_dargs = {'ignore_status': False, 'debug': True}
    auto_indexes_dict = {}
    auto_index = params.get('auto_index', 'no') == 'yes'
    auto_slot = params.get('auto_slot', 'no') == 'yes'

    if index and index_second:
        if int(index) > int(index_second):
            test.error("Invalid parameters")

    vm = env.get_vm(vm_name)
    vm_xml = VMXML.new_from_inactive_dumpxml(vm_name)
    vm_xml_backup = vm_xml.copy()

    try:
        if remove_contr:
            vm_xml.remove_all_device_by_type('controller')
        if remove_address:
            remove_devices(vm_xml, 'address')
        remove_devices(vm_xml, 'usb')
        if remove_nic:
            remove_devices(vm_xml, 'interface')
        # Get the max controller index in current vm xml
        if add_contrl_list:
            ret_indexes = libvirt_pcicontr.get_max_contr_indexes(vm_xml, 'pci', 'pcie-root-port')
            if ret_indexes and len(ret_indexes) > 0:
                if auto_bus:
                    new_index = "0x%02x" % (int(ret_indexes[0]) + 1)
                    add_contrl_list = re.sub(r"'bus': '%s'", "'bus': '%s'" % new_index, add_contrl_list, count=5)
                    logging.debug("bus is set automatically with %s", new_index)
                if auto_slot:
                    available_slot = libvirt_pcicontr.get_free_pci_slot(vm_xml)
                    if not available_slot:
                        test.error("No pci slot is available any more. Please check your vm xml.")
                    add_contrl_list = re.sub(r"'slot': '%s'", "'slot': '%s'" % available_slot, add_contrl_list, count=5)
                    logging.debug("slot is set automatically with %s", available_slot)
                if auto_index:
                    new_index = int(ret_indexes[0]) + 1
                    add_contrl_list = re.sub(r"'index': '%s'", "'index': '%s'" % new_index, add_contrl_list, count=5)
                    logging.debug("index is set automatically with %s", new_index)
        logging.debug("Now add_contrl_list=%s", add_contrl_list)

        if setup_controller == "yes":
            if add_contrl_list:
                contrls = eval(add_contrl_list)
                for one_contrl in contrls:
                    contr_dict = {}
                    cntl_target = ''
                    if 'model' in one_contrl:
                        contr_dict.update({'controller_model': one_contrl['model']})
                    if 'busNr' in one_contrl:
                        cntl_target = "{'busNr': %s}" % one_contrl['busNr']
                    if 'chassisNr' in one_contrl:
                        cntl_target += "{'chassisNr': '%s'}" % one_contrl['chassisNr']
                    if 'alias' in one_contrl:
                        contr_dict.update({'contr_alias': one_contrl['alias']})
                    if 'type' in one_contrl:
                        contr_dict.update({'controller_type': one_contrl['type']})
                    else:
                        contr_dict.update({'controller_type': 'pci'})
                    if 'node' in one_contrl:
                        contr_dict.update({'controller_node': one_contrl['node']})
                    if 'index' in one_contrl:
                        contr_dict.update({'controller_index': one_contrl['index']})
                    contr_dict.update({'controller_target': cntl_target})
                    addr = None
                    if 'bus' in one_contrl:
                        addr = '{"bus": %s' % one_contrl['bus']
                        if 'slot' in one_contrl:
                            addr = addr + ', "slot": %s' % one_contrl['slot']
                            if 'func' in one_contrl:
                                addr = addr + ', "function": %s' % one_contrl['func']
                        addr = addr + '}'
                    if addr:
                        contr_dict.update({'controller_addr': addr})
                    logging.debug(contr_dict)
                    controller_add = libvirt.create_controller_xml(contr_dict)
                    vm_xml.add_device(controller_add)
                    logging.debug("Add a controller: %s" % controller_add)
            else:
                if index_second:
                    setup_controller_xml(index_second)
                setup_controller_xml(index, addr_str)
                if second_level_controller_num:
                    for indx in range(2, int(second_level_controller_num) + 2):
                        addr_second = "0%s:0%s.0" % (index, str(indx))
                        setup_controller_xml(str(indx), addr_second)

        setup_os_xml()
        if int(pci_bus_number) > 0:
            address_params = {'bus': "%0#4x" % int(pci_bus_number), 'slot': "%0#4x" % int(pci_bus_number)}
            libvirt.set_disk_attr(vm_xml, 'vda', 'address', address_params)
        if cpu_numa_cells:
            if not vm_xml.cpu:
                vmxml_cpu = VMCPUXML()
                vmxml_cpu.xml = "<cpu mode='host-model'><numa/></cpu>"
            else:
                vmxml_cpu = vm_xml.cpu
                logging.debug("Existing cpu configuration in guest xml:\n%s", vmxml_cpu)
                vmxml_cpu.mode = 'host-model'
                vmxml_cpu.remove_elem_by_xpath('/model')
                vmxml_cpu.remove_elem_by_xpath('/numa')
            vmxml_cpu.numa_cell = VMCPUXML.dicts_to_cells(eval(cpu_numa_cells))
            vm_xml.cpu = vmxml_cpu
            vm_xml.vcpu = int(params.get('vcpu_count', 4))
        if sound_dict:
            add_device_xml(vm_xml, 'sound', sound_dict)
        if balloon_dict:
            add_device_xml(vm_xml, 'memballoon', balloon_dict)

        logging.debug("Test VM XML before define is %s" % vm_xml)
        if not define_and_check(vm_xml):
            logging.debug("Can't define the VM, exiting.")
            return
        vm_xml = VMXML.new_from_dumpxml(vm_name)
        logging.debug("Test VM XML after define is %s" % vm_xml)
        if auto_index:
            contrls = eval(add_contrl_list)
            for one_contrl in contrls:
                ret_indexes = libvirt_pcicontr.get_max_contr_indexes(vm_xml,
                                                                     one_contrl.get('type', 'pci'),
                                                                     one_contrl.get('model'))
                auto_indexes_dict.update({one_contrl['model']: ret_indexes})
        if check_contr_addr:
            check_controller_addr(cntlr_bus)
        if new_pcie_root_port_model and old_pcie_root_port_model:
            if utils_misc.compare_qemu_version(2, 9, 0, False):
                expect_model = new_pcie_root_port_model
            else:
                expect_model = old_pcie_root_port_model
            logging.debug("Expect the model for 'pcie-root-port': "
                          "%s" % expect_model)
            check_dict = {'modelname': expect_model}
            check_cntrl(vm_xml, 'pci', 'pcie-root-port',
                        '2', check_dict, False)
        if check_cntrls_list:
            for check_one in eval(check_cntrls_list):
                logging.debug("The controller to be checked: {}".format(check_one))
                check_cntrl(vm_xml, check_one.get('type', 'pci'), check_one.get('model'),
                            check_one.get('index'), check_one, False)
        if run_vm:
            try:
                if not start_and_check():
                    logging.debug("Can't start the VM, exiting.")
                    return
            except virt_vm.VMStartError as detail:
                test.fail(detail)

        # Need coldplug/hotplug
        if attach_option:
            attach_device(vm_name)
            vm_xml = VMXML.new_from_dumpxml(vm_name)
            logging.debug("Guest xml after attaching device:{}".format(vm_xml))
            # Check device's bus if needed
            if check_dev_bus:
                buses = get_device_bus(vm_xml, attach_dev_type)
                if len(buses) == 0:
                    test.fail("No bus was found")
                if buses[0] != params.get("expect_bus"):
                    test.fail("The expected bus for device is {}, "
                              "but found {}".format(params.get("expect_bus"),
                                                    buses[0]))
        if qemu_monitor_cmd:
            check_detach_attach_result(vm_name,
                                       qemu_monitor_cmd,
                                       params.get("qemu_monitor_pattern"),
                                       None)
        # Check guest xml
        if attach_dev_type == 'interface' and 'e1000e' in attach_option:
            cntls = vm_xml.get_controllers(controller_type='pci', model='pcie-root-port')
            cntl_index_list = []
            for cntl in cntls:
                cntl_index_list.append(cntl.get('index'))
            logging.debug("All pcie-root-port controllers' "
                          "index: {}".format(cntl_index_list))
            bus_list = get_device_bus(vm_xml, "interface")
            for bus in bus_list:
                if str(int(bus, 16)) not in cntl_index_list:
                    test.fail("The attached NIC with bus '{}' is not attached "
                              "to any pcie-root-port by default".format(bus))
        if check_qemu:
            if qemu_patterns:
                if auto_index:
                    index_str = "%x" % int(auto_indexes_dict['pcie-root-port'][0])
                    qemu_patterns = qemu_patterns % index_str
                    logging.debug("qemu_patterns=%s", qemu_patterns)
                if qemu_patterns.count('multifunction=on'):
                    check_multifunction()
                search_qemu_cmd = eval(qemu_patterns)
                logging.debug(search_qemu_cmd)
            else:
                search_qemu_cmd = get_search_patt_qemu_line()
            check_qemu_cmdline(search_pattern=search_qemu_cmd)
            vm.wait_for_login().close()

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
                elif guest_patterns:
                    check_guest_by_pattern(guest_patterns)
                elif cmd_in_guest:
                    check_guest_by_cmd(eval(cmd_in_guest))
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
        # Need hotunplug
        if detach_option:
            detach_device(vm_name)
            if qemu_monitor_cmd:
                check_detach_attach_result(vm_name,
                                           qemu_monitor_cmd,
                                           params.get("qemu_monitor_pattern"),
                                           "")
            if cmd_in_guest:
                check_guest_by_cmd(eval(cmd_in_guest), expect_error=True)

    finally:
        vm_xml_backup.sync()
