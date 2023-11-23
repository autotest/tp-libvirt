from virttest import utils_vdpa
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.sriov import sriov_base


def run(test, params, env):
    """
    Check the mem_lock limit when there is a hostdev and vDPA interface together.
    """
    def setup_test(dev_type):
        """
        Set up test.

        1) Remove interface devices and iommu device
        2) Set VM attrs
        3) Add an vDPA interface

        :param dev_type: interface type
        """
        test.log.info("TEST_SETUP: Remove iommu and interface devices.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'iommu')

        test.log.info("TEST_SETUP: Update VM attrs.")
        vm_attrs = eval(params.get('vm_attrs', '{}'))
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        test.log.debug("Updated VM xml: %s.",
                       vm_xml.VMXML.new_from_dumpxml(vm_name))

        test.log.info("TEST_SETUP: Add an interface to the VM.")
        iface_dict = eval(params.get('iface_dict', "{'source': {'dev':'/dev/vhost-vdpa-0'}}"))
        iface_dev = interface_base.create_iface(dev_type, iface_dict)
        libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev)
        test.log.debug("VM xml after updating ifaces: %s.",
                       vm_xml.VMXML.new_from_dumpxml(vm_name))

        if test_scenario == "cold_plug":
            test.log.info("TEST_SETUP: Attach hostdev device to the VM.")
            hostdev_dict = eval(params.get("hostdev_dict", "{}"))
            sriov_test_obj.vf_pci_addr.pop("type")
            hostdev_dict.update(
                {'source': {'untyped_address': sriov_test_obj.vf_pci_addr}})
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm_name), "hostdev", hostdev_dict)

    def run_test(dev_type):
        """
        Check the locked memory for the vm with hostdev and vDPA interfaces.

        1. The locked memory will be "1G + current memory * ${num of vDPA interface}";
        2. All hostdev interfaces will share one memory space, so the locked
            memory will be "current memory(for hostdev interfaces) + current
            memory * num of vDPA interface (for vDPA) + 1G";
        """
        test.log.info("TEST_STEP: Start the vm with a vDPA interface.")
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_no = len(new_vmxml.devices.by_device_tag("hostdev")) + len(new_vmxml.devices.by_device_tag("interface"))
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) * iface_no + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK after VM startup!")

        if test_scenario == "cold_plug":
            return
        test.log.info("TEST_STEP: Hotplug a hostdev interface.")
        virsh.attach_interface(vm.name, "hostdev --managed %s" % sriov_test_obj.vf_pci,
                               debug=True, ignore_status=False)
        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) * 2 + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK after attaching a hostdev "
                      "interface!")

        test.log.info("TEST_STEP: Hotplug another hostdev interface.")
        virsh.attach_interface(vm.name, "hostdev --managed %s" % sriov_test_obj.vf_pci2,
                               debug=True, ignore_status=False)
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK after attaching a hostdev "
                      "interface!")

        test.log.info("TEST_STEP: Add one more vDPA interface.")
        iface2 = interface_base.create_iface(
            dev_type, eval(params.get(
                'iface_dict2', "{'source': {'dev':'/dev/vhost-vdpa-1'}}")))
        virsh.attach_device(vm_name, iface2.xml, **virsh_args)

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) * 3 + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK after attaching the 2nd "
                      "vDPA interface device!")

    # Variable assignment
    dev_type = params.get('dev_type', 'vdpa')
    test_scenario = params.get("test_scenario")
    virsh_args = {'debug': True, 'ignore_status': False}
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    try:
        pf_pci = utils_vdpa.get_vdpa_pci()
        test_env_obj = utils_vdpa.VDPAOvsTest(pf_pci)
        test_env_obj.setup()
        setup_test(dev_type)
        run_test(dev_type)

    finally:
        backup_vmxml.sync()
        test_env_obj.cleanup()
