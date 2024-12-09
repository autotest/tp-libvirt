from virttest import libvirt_version
from virttest import utils_net
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import controller
from virttest.libvirt_xml.devices import vsock
from virttest.utils_libvirt import libvirt_vmxml

from provider.viommu import viommu_base


def run(test, params, env):
    """
    Start guest with iommu enabled for various virtio devices.
    """

    def prepare_guest_xml(vmxml):
        """
        Prepare guest xml with iommu enabled for various virtio devices

        :params vmxml: the guest xml
        """
        for device_type in devices_list_1:
            if device_type == "interface":
                libvirt_vmxml.modify_vm_device(vmxml, device_type, interface_driver_dict)
            elif device_type == "input":
                for input_num in range(0, 3):
                    libvirt_vmxml.modify_vm_device(vmxml, device_type, common_driver_dict, input_num)
            else:
                libvirt_vmxml.modify_vm_device(vmxml, device_type, common_driver_dict)
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        for device_type in devices_list_2:
            if device_type == "controller":
                controller_dicts = {"scsi": scsi_controller_dict,  "virtio-serial": virtio_serial_controller_dict}
                for controller_device in ["scsi", "virtio-serial"]:
                    controller_dict = controller_dicts.get(controller_device)
                    controller_dev = controller.Controller()
                    controller_dev.setup_attrs(**controller_dict)
                    vmxml.add_device(controller_dev)
                    vmxml.sync()
            else:
                vsock_dev = vsock.Vsock()
                vsock_dev.setup_attrs(**vsock_dict)
                vmxml.add_device(vsock_dev)
                vmxml.sync()

    vm_name = params.get("main_vm")
    ping_outside = params.get("ping_outside")
    common_driver_dict = eval(params.get("common_driver_dict", "{}"))
    iommu_dict = eval(params.get("iommu_dict", "{}"))
    interface_driver_dict = eval(params.get("interface_driver_dict", "{}"))
    scsi_controller_dict = eval(params.get("scsi_controller_dict", "{}"))
    virtio_serial_controller_dict = eval(params.get("virtio_serial_controller_dict", "{}"))
    vsock_dict = eval(params.get("vsock_dict", "{}"))
    devices_list_1 = eval(params.get("devices_list_1"))
    devices_list_2 = eval(params.get("devices_list_2"))
    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    bkxml = vmxml.copy()
    test_obj = viommu_base.VIOMMUTest(vm, test, params)

    try:
        test_obj.setup_iommu_test(iommu_dict=iommu_dict)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        prepare_guest_xml(vmxml)
        if not vm.is_alive():
            vm.start()
        test.log.debug("The current guest xml is %s", virsh.dumpxml(vm_name).stdout_text)
        vm_session = vm.wait_for_serial_login()
        utils_net.ping(dest=ping_outside, count='3', timeout=10, session=vm_session, force_ipv4=True)
        vm_session.close()
    finally:
        bkxml.sync()
        test_obj.teardown_iommu_test()
