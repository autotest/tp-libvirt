import os
import re
import logging

from avocado.utils import download

from virttest import data_dir
from virttest import virsh
from virttest import utils_misc
from virttest import libvirt_version

from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt

from src.virtio_transitional import virtio_transitional_base


def run(test, params, env):
    """
    Test virtio/virtio-transitional/virtio-non-transitional model of vsock

    :param test: Test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """
    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    add_pcie_to_pci_bridge = params.get("add_pcie_to_pci_bridge")
    guest_src_url = params.get("guest_src_url")
    virtio_model = params['virtio_model']
    boot_with_vsock = (params.get('boot_with_vsock', 'yes') == 'yes')
    hotplug = (params.get('hotplug', 'no') == 'yes')
    addr_pattern = params['addr_pattern']
    device_pattern = params['device_pattern']

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")

    def check_vsock_inside_guest():
        """
        check vsock device inside guest
        """
        lspci_cmd = 'lspci'
        lspci_output = session.cmd_output(lspci_cmd)
        device_str = re.findall(r'%s\s%s' % (addr_pattern, device_pattern),
                                lspci_output)
        if not device_str:
            test.fail('lspci failed, no device "%s"' % device_pattern)

    # Download and replace image when guest_src_url provided
    if guest_src_url:
        image_name = params['image_path']
        target_path = utils_misc.get_path(data_dir.get_data_dir(), image_name)
        if not os.path.exists(target_path):
            download.get_file(guest_src_url, target_path)
        params["blk_source_name"] = target_path

    # Add pcie-to-pci-bridge when it is required
    if add_pcie_to_pci_bridge:
        pci_controllers = vmxml.get_controllers('pci')
        for controller in pci_controllers:
            if controller.get('model') == 'pcie-to-pci-bridge':
                break
        else:
            contr_dict = {'controller_type': 'pci',
                          'controller_model': 'pcie-to-pci-bridge'}
            cntl_add = libvirt.create_controller_xml(contr_dict)
            libvirt.add_controller(vm_name, cntl_add)

    # Generate xml for device vsock
    vsock_xml = libvirt.create_vsock_xml(virtio_model)
    if boot_with_vsock:  # Add vsock xml to vm only when needed
        libvirt.add_vm_device(vmxml, vsock_xml)
    try:
        if (params["os_variant"] == 'rhel6' or
                'rhel6' in params.get("shortname")):
            # Update interface to virtio-transitional mode for
            # rhel6 guest to make it works for login
            iface_params = {'model': 'virtio-transitional'}
            libvirt.modify_vm_iface(vm_name, "update_iface", iface_params)
        libvirt.set_vm_disk(vm, params)
        if hotplug:
            file_arg = vsock_xml.xml
            with open(file_arg) as vsock_file:
                logging.debug("Attach vsock by XML: %s", vsock_file.read())
            s_attach = virsh.attach_device(vm_name, file_arg, debug=True)
            libvirt.check_exit_status(s_attach)
        if add_pcie_to_pci_bridge:
            # Check device should be plug to right bus
            virtio_transitional_base.check_plug_to(vm_name, 'vsock')
        session = vm.wait_for_login()
        check_vsock_inside_guest()
        if hotplug:
            with open(file_arg) as vsock_file:
                logging.debug("Detach vsock by XML: %s", vsock_file.read())
            s_detach = virsh.detach_device(vm_name, file_arg, debug=True)
            libvirt.check_exit_status(s_detach)
    finally:
        vm.destroy()
        backup_xml.sync()
