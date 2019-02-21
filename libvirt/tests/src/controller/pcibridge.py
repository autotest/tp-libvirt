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
    """
    vm_name = params.get('main_vm')
    pci_model = params.get('pci_model', 'pci')
    hotplug = 'yes' == params.get('hotplug', 'no')

    pci_model_name = params.get('pci_model_name')
    pci_br_has_device = 'yes' == params.get('pci_br_has_device', 'no')
    sound_dev_model_type = params.get('sound_dev_model_type', '')
    sound_dev_address = params.get('sound_dev_address', '')
    iface_model = params.get('iface_model', '')
    iface_source = params.get('iface_source', '')

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    bkxml = vmxml.copy()
    vm = env.get_vm(vm_name)

    try:

        # Check if there is a pci/pcie-to-pci bridge, if so,
        # just use the existing pci/pcie-to-pci-bridge to test
        ori_pci_br = [dev for dev in vmxml.get_devices('controller')
                      if dev.type == 'pci' and dev.model == pci_model]

        # If there is not a pci/pcie-to-pci bridge to test,
        # create one and add to vm
        if not ori_pci_br:
            logging.info('No %s on vm, create one', pci_model)
            pci_bridge = Controller('pci')
            pci_bridge.model = pci_model
            pci_bridge.model_name = {'name': pci_model_name}

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
        logging.debug(pci_br)
        pci_br_index = pci_br.index

        # If test scenario requires another pci device on pci/pcie-to-pci
        # bridge before hotplug, add a sound device and make sure
        # the 'bus' is same with pci bridge index
        if pci_br_has_device:
            sound_dev = Sound()
            sound_dev.model_type = sound_dev_model_type
            sound_dev.address = eval(sound_dev_address % pci_br_index)
            logging.debug(sound_dev.address)
            vmxml.add_device(sound_dev)
            vmxml.sync()

        # Test hotplug scenario
        if hotplug:
            vm.start()
            vm.wait_for_login().close()

            # Create interface to be hotplugged
            logging.info('Create interface to be hotplugged')
            iface = Interface('network')
            iface.model = iface_model
            iface.source = eval(iface_source)
            mac = utils_net.generate_mac_address_simple()
            iface.mac_address = mac
            logging.debug(iface)

            result = virsh.attach_device(vm_name, iface.xml, debug=True)
            libvirt.check_exit_status(result)

            xml_after_attach = VMXML.new_from_dumpxml(vm_name)
            logging.debug(virsh.dumpxml(vm_name))

            # Check if the iface with given mac address is successfully attached
            iface_list = [
                iface for iface in xml_after_attach.get_devices('interface')
                if iface.mac_address == mac
            ]

            logging.debug('iface list after attach: %s', iface_list)
            if not iface_list:
                test.error('Failed to attach interface %s' % iface)

            # Check inside vm
            def check_inside_vm(session, expect=True):
                ip_output = session.cmd('ip a')
                logging.debug(ip_output)

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

            logging.debug(virsh.dumpxml(vm_name))

            # Check if the iface with given mac address has been
            # successfully detached
            xml_after_detach = VMXML.new_from_dumpxml(vm_name)
            iface_list_after_detach = [
                iface for iface in xml_after_detach.get_devices('interface')
                if iface.mac_address == mac
            ]

            logging.debug('iface list after detach: %s', iface_list_after_detach)
            if iface_list_after_detach:
                test.fail('Failed to detach device: %s', iface)

            # Check again inside vm
            session = vm.wait_for_serial_login()
            if not utils_misc.wait_for(lambda: check_inside_vm(session, False),
                                       timeout=60, step=5):
                test.fail('Check interface inside vm failed,'
                          'interface not successfully detached:'
                          'found mac address %s' % mac)
            session.close()

    finally:
        bkxml.sync()
