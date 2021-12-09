import os
import aexpect

from avocado.utils import download

from virttest import data_dir
from virttest import utils_misc
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.libvirt_xml.devices.channel import Channel
from virttest.libvirt_xml.devices.console import Console


def run(test, params, env):
    """
    Test virtio/virtio-transitional model of serial device

    :param test: Test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """

    def get_free_pci_slot():
        """
        Get a free slot from pcie-to-pci-bridge

        :return: The free slot
        """
        used_slot = []
        for dev in pci_devices:
            address = dev.find('address')
            if (address is not None and
                    address.get('bus') == pci_bridge_index):
                used_slot.append(address.get('slot'))
        for slot_index in range(1, 30):
            slot = "%0#4x" % slot_index
            if slot not in used_slot:
                return slot
        return None

    def test_data_transfer(dev_type):
        """
        Test data transfer between guest and host via console/serial device

        :param dev_type: The device type to be tested, console or channel
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        console_xml = vmxml.xmltreefile.find('devices').find(dev_type)
        host_path = console_xml.find('source').get('path')
        guest_path = '/dev/hvc0' if dev_type == 'console' else '/dev/vport0p1'
        test_message = 'virtiochannel'
        cat_cmd = "cat %s" % host_path
        logfile = "test_data_transfer-%s.log" % dev_type
        host_process = aexpect.ShellSession(cat_cmd, auto_close=False,
                                            output_func=utils_misc.log_line,
                                            output_params=(logfile,))
        guest_session = vm.wait_for_login()
        guest_session.cmd_output('echo %s > %s' % (test_message, guest_path))
        guest_session.close()
        try:
            host_process.read_until_last_line_matches(test_message, timeout=10)
        except aexpect.exceptions.ExpectError as e:
            test.fail('Did not catch the expected output from host side,'
                      ' the detail of the failure: %s' % str(e))
        finally:
            host_process.close()

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    add_pcie_to_pci_bridge = params.get("add_pcie_to_pci_bridge")
    guest_src_url = params.get("guest_src_url")
    virtio_model = params['virtio_model']

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")

    # Download and replace image when guest_src_url provided
    if guest_src_url:
        image_name = params['image_path']
        target_path = utils_misc.get_path(data_dir.get_data_dir(), image_name)
        if not os.path.exists(target_path):
            download.get_file(guest_src_url, target_path)
        params["blk_source_name"] = target_path

    try:
        # Add pcie-to-pci-bridge when it is required
        if add_pcie_to_pci_bridge:
            pci_controllers = vmxml.get_controllers('pci')
            for controller in pci_controllers:
                if controller.get('model') == 'pcie-to-pci-bridge':
                    pci_bridge = controller
                    break
            else:
                contr_dict = {'controller_type': 'pci',
                              'controller_model': 'pcie-to-pci-bridge'}
                pci_bridge = libvirt.create_controller_xml(contr_dict)
                libvirt.add_controller(vm_name, pci_bridge)
                pci_bridge = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)\
                    .get_controllers('pci', 'pcie-to-pci-bridge')[0]
            pci_bridge_index = '%0#4x' % int(pci_bridge.get("index"))

        # Update interface to virtio-transitional mode for
        # rhel6 guest to make it works for login
        iface_params = {'model': 'virtio-transitional'}
        libvirt.modify_vm_iface(vm_name, "update_iface", iface_params)
        libvirt.set_vm_disk(vm, params)
        # vmxml will not be updated since set_vm_disk
        # sync with another dumped xml inside the function
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Remove all current serial devices
        vmxml.remove_all_device_by_type('serial')
        vmxml.remove_all_device_by_type('channel')
        vmxml.remove_all_device_by_type('console')
        vmxml.del_controller('virtio-serial')
        vmxml.sync()

        # Add virtio-serial with right model
        contr_dict = {'controller_type': 'virtio-serial',
                      'controller_model': virtio_model}
        if add_pcie_to_pci_bridge:
            pci_devices = vmxml.xmltreefile.find('devices').getchildren()
            slot = get_free_pci_slot()
            addr = '{"bus": %s, "slot": %s}' % (pci_bridge_index, slot)
            contr_dict.update({'controller_addr': addr})
        cntl_add = libvirt.create_controller_xml(contr_dict)
        libvirt.add_controller(vm_name, cntl_add)
        # vmxml will not be updated since set_vm_disk
        # sync with another dumped xml inside the function
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Add channel and console device attached to virtio-serial bus
        target_dict = {'type': 'virtio',
                       'name': 'ptychannel'}
        address_dict = {'type': 'virtio-serial',
                        'controller': '0',
                        'bus': '0'}
        channel_xml = Channel('pty')
        channel_xml.target = target_dict
        channel_xml.address = address_dict
        console_xml = Console()
        console_xml.target_port = '0'
        console_xml.target_type = 'virtio'
        vmxml.add_device(channel_xml)
        vmxml.add_device(console_xml)
        vmxml.sync()
        if vm.is_alive():
            vm.destroy()
        vm.start(autoconsole=False)

        # Test data transfer via console and channel devices
        test_data_transfer('console')
        test_data_transfer('channel')
    finally:
        vm.destroy()
        backup_xml.sync()

        if guest_src_url and target_path:
            libvirt.delete_local_disk("file", path=target_path)
