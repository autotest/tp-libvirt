import os
import logging

from avocado.utils import download
from avocado.utils import process

from virttest import data_dir
from virttest import virsh
from virttest import utils_misc
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test virtio/virtio-transitional/virtio-non-transitional model of rng

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

    def get_free_root_port():
        """
        Get a free root port for rng device

        :return: The bus index of free root port
        """
        root_ports = set()
        other_ports = set()
        used_slot = set()
        # Record the bus indexes for all pci controllers
        for controller in pci_controllers:
            if controller.get('model') == 'pcie-root-port':
                root_ports.add(controller.get('index'))
            else:
                other_ports.add(controller.get('index'))
        # Record the addresses being allocated for all pci devices
        pci_devices = vmxml.xmltreefile.find('devices').getchildren()
        for dev in pci_devices:
            address = dev.find('address')
            if address is not None:
                used_slot.add(address.get('bus'))
        # Find the bus address unused
        for bus_index in root_ports:
            bus = "%0#4x" % int(bus_index)
            if bus not in used_slot:
                return bus
        # Add a new pcie-root-port if no free one
        for index in range(1, 30):
            if index not in (root_ports | other_ports):
                contr_dict = {'controller_type': 'pci',
                              'controller_index': index,
                              'controller_model': 'pcie-root-port'}
                cntl_add = libvirt.create_controller_xml(contr_dict)
                libvirt.add_controller(vm_name, cntl_add)
                return "%0#4x" % int(index)
        return None

    def check_plug_to(bus_type='pcie-to-pci-bridge'):
        """
        Check if the nic is plugged onto pcie-to-pci-bridge

        :param bus_type:  The bus type been expected to plug to
        :return True if plugged onto 'bus_type', otherwise False
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        rng = vmxml.xmltreefile.find('devices').find('rng')
        bus = int(eval(rng.find('address').get('bus')))
        controllers = vmxml.get_controllers('pci')
        for controller in controllers:
            if controller.get('index') == bus:
                if controller.get('model') == bus_type:
                    return True
                break
        return False

    def check_rng_inside_guest():
        """
        check rng device inside guest
        """
        check_cmd = params['check_cmd']
        lspci_output = session.cmd_output(check_cmd)
        session.cmd_output('pkill -9 hexdump')
        if 'No such file or directory' in lspci_output and device_exists:
            test.fail('Can not detect device by %s.' % check_cmd)

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    guest_src_url = params.get("guest_src_url")
    virtio_model = params['virtio_model']
    boot_with_rng = (params.get('boot_with_rng', 'yes') == 'yes')
    hotplug = (params.get('hotplug', 'no') == 'yes')
    device_exists = (params.get('device_exists', 'yes') == 'yes')
    plug_to = params.get('plug_to', '')

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")

    # Download and update image if required
    if guest_src_url:
        image_name = params['image_path']
        target_path = utils_misc.get_path(data_dir.get_data_dir(), image_name)
        if not os.path.exists(target_path):
            download.get_file(guest_src_url, target_path)
        params["blk_source_name"] = target_path

    try:
        # Add 'pcie-to-pci-bridge' if there is no one
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

        # Update nic and vm disks
        if (params["os_variant"] == 'rhel6' or
                'rhel6' in params.get("shortname")):
            iface_params = {'model': 'virtio-transitional'}
            libvirt.modify_vm_iface(vm_name, "update_iface", iface_params)
        libvirt.set_vm_disk(vm, params)
        # vmxml will not be updated since set_vm_disk
        # sync with another dumped xml inside the function
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)

        # Remove existed rng devices if there are
        rng_devs = vmxml.get_devices('rng')
        for rng in rng_devs:
            vmxml.del_device(rng)
        vmxml.xmltreefile.write()
        vmxml.sync()

        # General new rng xml per configurations
        rng_xml = libvirt.create_rng_xml({"rng_model": virtio_model})
        if params.get('specify_addr', 'no') == 'yes':
            pci_devices = vmxml.xmltreefile.find('devices').getchildren()
            addr = rng_xml.new_rng_address()
            if plug_to == 'pcie-root-port':
                bus = get_free_root_port()
                addr.set_attrs({'bus': bus})
            else:
                slot = get_free_pci_slot()
                addr.set_attrs({'bus': pci_bridge_index, 'slot': slot})
            rng_xml.address = addr
        if boot_with_rng:  # Add to vm if required
            libvirt.add_vm_device(vmxml, rng_xml)
        if not vm.is_alive():
            vm.start()
        if hotplug:  # Hotplug rng if required
            file_arg = rng_xml.xml
            with open(file_arg) as rng_file:
                logging.debug("Attach rng by XML: %s", rng_file.read())
            s_attach = virsh.attach_device(vm_name, file_arg, debug=True)
            libvirt.check_exit_status(s_attach)
            check_plug_to(plug_to)
        session = vm.wait_for_login()
        check_rng_inside_guest()
        if hotplug:  # Unplug rng if hotplugged previously
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            rng = vmxml.get_devices('rng')[0]
            file_arg = rng.xml
            with open(file_arg) as rng_file:
                logging.debug("Detach rng by XML: %s", rng_file.read())
            s_detach = virsh.detach_device(vm_name, file_arg, debug=True)
            libvirt.check_exit_status(s_detach)
        if not hotplug:
            session.close()
            save_path = os.path.join(
                data_dir.get_tmp_dir(), '%s.save' % params['os_variant'])
            ret = virsh.save(vm_name, save_path)
            libvirt.check_exit_status(ret)
            ret = virsh.restore(save_path)
            libvirt.check_exit_status(ret)
            session = vm.wait_for_login()
            check_rng_inside_guest()
            process.run('rm -f %s' % save_path, ignore_status=True)
    finally:
        vm.destroy()
        backup_xml.sync()

        if guest_src_url and target_path:
            libvirt.delete_local_disk("file", path=target_path)
