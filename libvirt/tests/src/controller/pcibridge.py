import logging

from virttest import virsh
from virttest import utils_misc
from virttest import utils_net
from virttest.utils_test import libvirt
from virttest.libvirt_xml.vm_xml import VMXML
from virttest.libvirt_xml.devices.controller import Controller
from virttest.libvirt_xml.devices.sound import Sound
from virttest.libvirt_xml.devices.interface import Interface


def run(test, params, env):
    """
    Test pci/pcie-to-pci bridge

    Hotplug interface to pci/pcie-to-pci bridge, then check xml and
    inside vm.
    Hotunplug interface, then check xml and inside vm
    Other test scenarios of pci/pcie-to-pci bridge
    """

    def create_pci_device(pci_model, pci_model_name, **kwargs):
        """
        Create a pci/pcie bridge

        :param pci_model: model of pci controller device
        :param pci_model_name: model name of pci controller device
        :param kwargs: other k-w args that needed to create device
        :return: the newly created device object
        """
        pci_bridge = Controller('pci')
        pci_bridge.model = pci_model
        pci_bridge.model_name = {'name': pci_model_name}
        if 'index' in kwargs:
            pci_bridge.index = kwargs['index']
        if 'address' in kwargs:
            pci_bridge.address = pci_bridge.new_controller_address(
                attrs=eval(kwargs['address']))

        logging.debug('pci_bridge: %s', pci_bridge)
        return pci_bridge

    def create_iface(iface_model, iface_source, **kwargs):
        """
        Create an interface to be attached to vm

        :param iface_model: model of the interface device
        :param iface_source: source of the interface device
        :param kwargs: other k-w args that needed to create device
        :return: the newly created interface object
        """
        iface = Interface('network')
        iface.model = iface_model
        iface.source = eval(iface_source)

        if 'mac' in kwargs:
            iface.mac_address = kwargs['mac']
        else:
            mac = utils_net.generate_mac_address_simple()
            iface.mac_address = mac

        if 'address' in kwargs:
            iface.address = iface.new_iface_address(attrs=eval(kwargs['address']))

        logging.debug('iface: %s', iface)
        return iface

    vm_name = params.get('main_vm')
    status_error = 'yes' == params.get('status_error', 'no')
    err_msg = params.get('err_msg', '')
    case = params.get('case', '')
    hotplug = 'yes' == params.get('hotplug', 'no')

    need_pci_br = 'yes' == params.get('need_pci_br', 'no')
    pci_model = params.get('pci_model', 'pci')
    pci_model_name = params.get('pci_model_name')
    pci_br_kwargs = eval(params.get('pci_br_kwargs', '{}'))

    pci_br_has_device = 'yes' == params.get('pci_br_has_device', 'no')
    sound_dev_model_type = params.get('sound_dev_model_type', '')
    sound_dev_address = params.get('sound_dev_address', '')

    iface_model = params.get('iface_model', '')
    iface_source = params.get('iface_source', '')
    iface_kwargs = eval(params.get('iface_kwargs', '{}'))

    max_slots = int(params.get('max_slots', 31))
    pcie_br_count = int(params.get('pcie_br_count', 3))

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    try:

        # Check if there is a pci/pcie-to-pci bridge, if so,
        # just use the existing pci/pcie-to-pci-bridge to test
        ori_pci_br = [dev for dev in vmxml.get_devices('controller')
                      if dev.type == 'pci' and dev.model == pci_model]

        if need_pci_br:
            # If there is not a pci/pcie-to-pci bridge to test,
            # create one and add to vm
            if not ori_pci_br:
                logging.info('No %s on vm, create one', pci_model)
                pci_bridge = create_pci_device(pci_model, pci_model_name)
                vmxml.add_device(pci_bridge)
                vmxml.sync()
                logging.debug(virsh.dumpxml(vm_name))

            # Check if pci/pcie-to-pci bridge is successfully added
            vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
            cur_pci_br = [dev for dev in vmxml.get_devices('controller')
                          if dev.type == 'pci' and dev.model == pci_model]
            if not cur_pci_br:
                test.error('Failed to add %s controller to vm xml' % pci_model)

            pci_br = cur_pci_br[0]
            logging.debug('pci_br: %s', pci_br)
            pci_br_index = pci_br.index

        # If test scenario requires another pci device on pci/pcie-to-pci
        # bridge before hotplug, add a sound device and make sure
        # the 'bus' is same with pci bridge index
        if need_pci_br and pci_br_has_device:
            sound_dev = Sound()
            sound_dev.model_type = sound_dev_model_type
            sound_dev.address = eval(sound_dev_address % pci_br_index)
            logging.debug('sound_dev.address: %s', sound_dev.address)
            vmxml.add_device(sound_dev)
            if case != 'vm_with_pcie_br_1_br':
                vmxml.sync()

        # Test hotplug scenario
        if hotplug:
            vm.start()
            vm.wait_for_login().close()

            # Create interface to be hotplugged
            logging.info('Create interface to be hotplugged')
            target_bus = cur_pci_br[0].index
            target_bus = hex(int(target_bus))
            logging.debug('target_bus: %s', target_bus)

            new_iface_kwargs = {'address': iface_kwargs['address'] % target_bus}
            logging.debug('address: %s', new_iface_kwargs['address'])
            iface = create_iface(iface_model, iface_source, **new_iface_kwargs)
            mac = iface.mac_address

            result = virsh.attach_device(vm_name, iface.xml, debug=True)
            libvirt.check_exit_status(result)

            xml_after_attach = VMXML.new_from_dumpxml(vm_name)
            logging.debug(virsh.dumpxml(vm_name))

            # Check if the iface with given mac address is successfully
            # attached with address bus equal to pcie/pci bridge's index
            iface_list = [
                iface for iface in xml_after_attach.get_devices('interface')
                if iface.mac_address == mac and
                int(iface.address['attrs']['bus'], 16) == int(pci_br_index, 16)
            ]

            logging.debug('iface list after attach: %s', iface_list)
            if not iface_list:
                test.error('Failed to attach interface %s' % iface)

            # Check inside vm
            def check_inside_vm(session, expect=True):
                ip_output = session.cmd('ip a')
                logging.debug('output of "ip a": %s', ip_output)

                return expect if mac in ip_output else not expect

            session = vm.wait_for_serial_login()
            if not utils_misc.wait_for(lambda: check_inside_vm(session, True),
                                       timeout=60, step=5):
                test.fail('Check interface inside vm failed,'
                          'interface not successfully attached:'
                          'not found mac address %s' % mac)
            session.close()

            # Test hotunplug
            result = virsh.detach_device(vm_name, iface.xml, debug=True)
            libvirt.check_exit_status(result)

            # Check if the iface with given mac address has been
            # successfully detached
            def is_hotunplug_interface_ok():
                xml_after_detach = VMXML.new_from_dumpxml(vm_name)
                iface_list_after_detach = [
                    iface for iface in xml_after_detach.get_devices('interface')
                    if iface.mac_address == mac
                ]
                logging.debug('iface list after detach: %s', iface_list_after_detach)
                return iface_list_after_detach == []

            if not utils_misc.wait_for(is_hotunplug_interface_ok, timeout=20):
                test.fail('Failed to detach device: %s' % iface)
            logging.debug(virsh.dumpxml(vm_name))

            # Check again inside vm
            session = vm.wait_for_serial_login()
            if not utils_misc.wait_for(lambda: check_inside_vm(session, False),
                                       timeout=60, step=5):
                test.fail('Check interface inside vm failed,'
                          'interface not successfully detached:'
                          'found mac address %s' % mac)
            session.close()

        # Other test scenarios of pci/pcie
        if case:
            logging.debug('iface_kwargs: %s', iface_kwargs)

            # Setting pcie-to-pci-bridge model name !=pcie-pci-bridge.
            # or Invalid controller index for pcie-to-pci-bridge.
            if case in ('wrong_model_name', 'invalid_index'):
                pci_bridge = create_pci_device(pci_model, pci_model_name,
                                               **pci_br_kwargs)
                vmxml.add_device(pci_bridge)
                result_to_check = virsh.define(vmxml.xml, debug=True)

            # Attach device with invalid slot to pcie-to-pci-bridge
            if case == 'attach_with_invalid_slot':
                iface = create_iface(iface_model, iface_source, **iface_kwargs)
                vmxml.add_device(iface)
                result_to_check = virsh.define(vmxml.xml, debug=True)

            # Test that pcie-to-pci-bridge has 31 available slots
            if case == 'max_slots':
                target_bus = cur_pci_br[0].index
                target_bus = hex(int(target_bus))
                logging.debug('target_bus: %s', target_bus)

                # Attach 32 interfaces
                for i in range(max_slots + 1):
                    logging.debug('address: %s', iface_kwargs['address'])
                    new_iface_kwargs = {'address': iface_kwargs['address']
                                        % (target_bus, hex(i + 1))}
                    iface = create_iface(iface_model, iface_source,
                                         **new_iface_kwargs)
                    logging.info('Attaching the %d th interface', i + 1)
                    result_in_loop = virsh.attach_device(
                        vm_name, iface.xml, flagstr='--config', debug=True)

                    # Attaching the 32rd interfaces will fail
                    if i == max_slots:
                        status_error = True
                    libvirt.check_exit_status(result_in_loop,
                                              expect_error=status_error)
                logging.debug(virsh.dumpxml(vm_name))

                # Get all devices on pcie-to-pci-bridge from new xml
                # Test if it matches with value of max_slots
                new_xml = VMXML.new_from_dumpxml(vm_name)
                device_on_pci_br = [
                    dev for dev in new_xml.get_devices('interface')
                    if dev.address['type_name'] == 'pci' and
                    int(dev.address['attrs']['bus'], 16) == int(target_bus, 16)
                ]

                logging.info('All slots of pcie-to-pci-bridge is %d',
                             len(device_on_pci_br))
                if len(device_on_pci_br) != max_slots:
                    test.fail('Max slots is %d instead of %d' %
                              (len(device_on_pci_br), max_slots))

            # Define a guest with pcie-to-pci-bridge controller's index <=bus
            if case.startswith('index_v_bus'):
                last_pci_index = max([
                    int(dev.index) for dev in vmxml.get_devices('controller')
                    if dev.type == 'pci'])

                # New index of new pcie-bridge should be +1
                new_index = last_pci_index + 1
                if case.endswith('less_than'):
                    new_bus = new_index + 1
                elif case.endswith('equal_to'):
                    new_bus = new_index
                else:
                    test.error('Invalid test: %s' % case)

                pci_br_kwargs['index'] = new_index
                pci_br_kwargs['address'] = pci_br_kwargs['address'] % (new_bus)
                logging.debug('pci_br_kwargs: %s', pci_br_kwargs)

                pcie_br = create_pci_device(pci_model, pci_model_name, **pci_br_kwargs)
                vmxml.add_device(pcie_br)
                result_to_check = virsh.define(vmxml.xml, debug=True)

            # Test define & start an VM with/without pcie-to-pci-bridge
            if case.startswith('vm_with_pcie_br'):
                if case.endswith('1_br'):
                    pass
                elif case.endswith('multi_br'):
                    # Add $pcie_br_count pcie-root-port to vm
                    for i in range(pcie_br_count):
                        temp_xml = VMXML.new_from_inactive_dumpxml(vm_name)
                        port = create_pci_device('pcie-root-port', 'pcie-root-port')
                        temp_xml.add_device(port)
                        temp_xml.sync()

                    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
                    pci_root_ports = [dev for dev in vmxml.get_devices('controller')
                                      if dev.type == 'pci' and
                                      dev.model == 'pcie-root-port']
                    indexs = sorted([hex(int(dev.index)) for dev in pci_root_ports])
                    logging.debug('indexs: %s', indexs)

                    # Add $pcie_br_count pcie-to-pci-bridge to vm,
                    # on separated pcie-root-port
                    for i in range(pcie_br_count):
                        new_kwargs = {
                            'address': pci_br_kwargs['address'] % indexs[-i - 1]}
                        pcie_br = create_pci_device(pci_model, pci_model_name,
                                                    **new_kwargs)
                        vmxml.add_device(pcie_br)

                elif case.endswith('no_br'):
                    # Delete all pcie-to-pci-bridge
                    for dev in ori_pci_br:
                        vmxml.del_device(dev)
                    vmxml.sync()

                    # Add an pci device(rtl8139 nic)
                    iface = create_iface(iface_model, iface_source)
                    vmxml.add_device(iface)
                else:
                    test.error('Invalid test: %s' % case)

                # Test define and start vm with new xml settings
                result_define_vm = virsh.define(vmxml.xml, debug=True)
                libvirt.check_exit_status(result_define_vm)
                result_start_vm = virsh.start(vm_name, debug=True)
                libvirt.check_exit_status(result_start_vm)

                # Login to make sure vm is actually started
                vm.create_serial_console()
                vm.wait_for_serial_login().close()

                logging.debug(virsh.dumpxml(vm_name))

                # Get all pcie-to-pci-bridge after test operations
                new_vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
                cur_pci_br = [dev for dev in new_vmxml.get_devices('controller')
                              if dev.type == 'pci' and dev.model == pci_model]
                logging.debug('cur_pci_br: %s', cur_pci_br)

                if case.endswith('multi_br'):
                    # Check whether all new pcie-to-pci-br are successfullly added
                    if len(cur_pci_br) - len(ori_pci_br) != pcie_br_count:
                        test.fail('Not all pcie-to-pci-br successfully added.'
                                  'Should be %d, actually is %d' %
                                  (pcie_br_count, len(cur_pci_br) - len(ori_pci_br)))

                elif case.endswith('no_br'):
                    # Check if a pcie-to-pci-br will be automatically created
                    if len(cur_pci_br) == 0:
                        test.fail('No new pcie-to-pci-bridge automatically created')

            # Check command result if there is a result to be checked
            if 'result_to_check' in locals():
                libvirt.check_exit_status(result_to_check, expect_error=status_error)
                libvirt.check_result(result_to_check, expected_fails=err_msg)

    finally:
        bkxml.sync()
