import os
import re
import logging

from avocado.utils import download

from virttest import virsh
from virttest import data_dir
from virttest import utils_misc
from virttest import libvirt_version
from virttest import utils_libvirtd

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml import devices
from virttest.utils_test import libvirt


def find_device(vm, params):
    """
    Find all disks in guest

    :param vm: libvirt_vm.VM object
    :param params: Dictionary with the test parameters
    :return: the list of device name
    """
    get_device_cmd = params.get("get_device_cmd", "ls /dev/[hsv]d[a-z]* | sort")
    get_device_pattern = params.get("get_device_pattern", "^/dev/[hsv]d[a-z]*$")
    session = vm.wait_for_login()
    output = session.cmd_output(get_device_cmd)
    session.close()
    return re.findall(get_device_pattern, output, re.M)


def get_new_device(list1, list2):
    """
    Get the different device between list1 and list2

    :param list1: List object contains devices
    :param list2: List object contains devices
    :return: The different/new device
    """
    return list(set(list2).difference(set(list1)))


def get_free_slot(bus_index, vmxml):
    """
    Get a free slot for given bus

    :param bus_index: the index of the bus to be searched, e.g. 0x08
    :param vmxml: vm_xml.VMXML object
    :return: The first free slot of the bus
    """
    if not bus_index:
        return None
    used_slot = []
    pci_devices = vmxml.xmltreefile.find('devices').findall('disk')
    pci_devices.extend(vmxml.get_controllers())
    for dev in pci_devices:
        address = dev.find('address')
        if address is not None and address.get('bus') == bus_index:
            used_slot.append(address.get('slot'))
    for slot_index in range(1, 30):
        slot = "%0#4x" % slot_index
        if slot not in used_slot:
            return slot
    return None


def run(test, params, env):
    """
    Test virtio/virtio-transitional/virtio-non-transitional model of disk

    :param test: Test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment
    """

    def reboot():
        """
        Shutdown and restart guest, then wait for login
        """
        vm.destroy()
        vm.start()
        vm.wait_for_login()

    def attach(xml, device_name, plug_method="hot"):
        """
        Attach device with xml, for both hot and cold plug

        :param xml: Device xml to be attached
        :param device_name: Device name to be attached
        :param plug_method: hot or cold for plug method
        """
        device_before_plug = find_device(vm, params)
        with open(xml) as disk_file:
            logging.debug("Attach disk by XML: %s", disk_file.read())
        file_arg = xml
        if plug_method == "cold":
            file_arg += ' --config'
        s_attach = virsh.attach_device(
            domainarg=vm_name, filearg=file_arg, debug=True)
        libvirt.check_exit_status(s_attach)
        if plug_method == "cold":
            reboot()
        detect_time = params.get("detect_disk_time", 20)
        plug_disks = utils_misc.wait_for(
            lambda: get_new_device(device_before_plug,
                                   find_device(vm, params)), detect_time)
        if not plug_disks:
            test.fail("Failed to hotplug device %s to guest" % device_name)

    def detach(xml, device_name, unplug_method="hot"):
        """
        Detach device with xml, for both hot and cold unplug

        :param xml: Device xml to be attached
        :param device_name: Device name to be attached
        :param plug_method: hot or cold for unplug method
        """
        with open(xml) as disk_file:
            logging.debug("Detach device by XML: %s", disk_file.read())
        file_arg = xml
        if unplug_method == "cold":
            file_arg = xml + ' --config'
        s_detach = virsh.detach_device(
            domainarg=vm_name, filearg=file_arg, debug=True)
        if unplug_method == "cold":
            reboot()
        libvirt.check_exit_status(s_detach)

    def attach_disk():  # pylint: disable=W0611
        """
        Sub test for attach disk, including hot and cold plug/unplug
        """
        plug_method = params.get("plug_method", "hot")
        device_source_format = params.get("at_disk_source_format", "raw")
        device_target = params.get("at_disk_target", "vdb")
        device_disk_bus = params.get("at_disk_bus", "virtio")
        device_source_name = params.get("at_disk_source", "attach.img")
        detect_time = params.get("detect_disk_time", 10)
        device_source_path = os.path.join(tmp_dir, device_source_name)
        device_source = libvirt.create_local_disk(
            "file", path=device_source_path,
            size="1", disk_format=device_source_format)

        def _generate_disk_xml():
            """Generate xml for device hotplug/unplug usage"""
            diskxml = devices.disk.Disk("file")
            diskxml.device = "disk"
            source_params = {"attrs": {'file': device_source}}
            diskxml.source = diskxml.new_disk_source(**source_params)
            diskxml.target = {'dev': device_target, 'bus': device_disk_bus}
            if params.get("disk_model"):
                diskxml.model = params.get("disk_model")
            if pci_bridge_index and device_disk_bus == 'virtio':
                addr = diskxml.new_disk_address('pci')
                addr.set_attrs({'bus': pci_bridge_index, 'slot': slot})
                diskxml.address = addr
            return diskxml.xml

        v_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        slot = get_free_slot(pci_bridge_index, v_xml)
        disk_xml = _generate_disk_xml()
        attach(disk_xml, device_target, plug_method)
        if plug_method == "cold":
            disk_xml = _generate_disk_xml()
        detach(disk_xml, device_target, plug_method)
        if not utils_misc.wait_for(
                lambda: not libvirt.device_exists(vm, device_target),
                detect_time):
            test.fail("Detach disk failed.")

    def attach_controller():  # pylint: disable=W0611
        """
        Sub test for attach controller
        """
        v_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        contr_index = len(v_xml.get_controllers('scsi'))
        contr_type = params.get("controller_type", 'scsi')
        contr_model = params.get("controller_model", "virtio-scsi")
        contr_dict = {'controller_type': contr_type,
                      'controller_model': contr_model,
                      'controller_index': contr_index}
        if pci_bridge_index:
            slot = get_free_slot(pci_bridge_index, v_xml)
            addr = '{"bus": %s, "slot": %s}' % (pci_bridge_index, slot)
            contr_dict.update({'controller_addr': addr})
        cntl_add = libvirt.create_controller_xml(contr_dict=contr_dict)
        attach(cntl_add.xml, params['controller_model'])
        cntl_add = libvirt.create_controller_xml(contr_dict=contr_dict)
        detach(cntl_add.xml, params['controller_model'])

    def snapshot():  # pylint: disable=W0611
        """
        Sub test for snapshot
        """
        for i in range(1, 4):
            ret = virsh.snapshot_create_as(vm_name, "sn%s --disk-only" % i)
            libvirt.check_exit_status(ret)
        libvirtd_obj = utils_libvirtd.Libvirtd()
        libvirtd_obj.restart()
        save_path = os.path.join(tmp_dir, "test.save")
        ret = virsh.save(vm_name, save_path)
        libvirt.check_exit_status(ret)
        ret = virsh.restore(save_path)
        libvirt.check_exit_status(ret)
        session = vm.wait_for_login()
        session.close()

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(params["main_vm"])
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    add_pcie_to_pci_bridge = params.get("add_pcie_to_pci_bridge")
    pci_bridge_index = None
    tmp_dir = data_dir.get_tmp_dir()
    guest_src_url = params.get("guest_src_url")

    if not libvirt_version.version_compare(5, 0, 0):
        test.cancel("This libvirt version doesn't support "
                    "virtio-transitional model.")

    if guest_src_url:
        image_name = params['image_path']
        target_path = utils_misc.get_path(data_dir.get_data_dir(), image_name)
        if not os.path.exists(target_path):
            download.get_file(guest_src_url, target_path)
        params["blk_source_name"] = target_path

    try:
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
        if (params["os_variant"] == 'rhel6' or
                'rhel6' in params.get("shortname")):
            iface_params = {'model': 'virtio-transitional'}
            libvirt.modify_vm_iface(vm_name, "update_iface", iface_params)
        libvirt.set_vm_disk(vm, params)
        if pci_bridge_index:
            v_xml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
            if params.get("disk_target_bus") == "scsi":
                scsi_controllers = v_xml.get_controllers('scsi')
                for index, controller in enumerate(scsi_controllers):
                    controller.find('address').set('bus', pci_bridge_index)
                    controller.find('address').set(
                        'slot', get_free_slot(pci_bridge_index, v_xml))
            else:
                disks = v_xml.get_devices(device_type="disk")
                for index, disk in enumerate(disks):
                    args = {'bus': pci_bridge_index,
                            'slot': get_free_slot(pci_bridge_index, v_xml)}
                    libvirt.set_disk_attr(
                        v_xml, disk.target['dev'],
                        'address', args)
            v_xml.xmltreefile.write()
            v_xml.sync()
        if not vm.is_alive():
            vm.start()
        vm.wait_for_login()
        test_step = params.get("sub_test_step")
        if test_step:
            eval(test_step)()
    finally:
        vm.destroy()
        libvirt.clean_up_snapshots(vm_name)
        backup_xml.sync()
        if guest_src_url and target_path:
            libvirt.delete_local_disk("file", path=target_path)
