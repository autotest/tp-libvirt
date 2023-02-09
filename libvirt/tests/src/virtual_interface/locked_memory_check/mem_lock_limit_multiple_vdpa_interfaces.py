from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_vdpa
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base


def run(test, params, env):
    """Hotplug memory with interface."""
    def setup_test(dev_type):
        """
        Set up test.

        1) Remove interface devices
        2) Set VM attrs
        3) Add an interface

        :param dev_type: interface type
        """
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')

        vm_attrs = eval(params.get('vm_attrs', '{}'))
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        test.log.debug("Updated VM xml: %s.",
                       vm_xml.VMXML.new_from_dumpxml(vm_name))

        iface_dict = eval(params.get('iface_dict', "{'source': {'dev':'/dev/vhost-vdpa-0'}}"))
        iface_dev = interface_base.create_iface(dev_type, iface_dict)
        libvirt.add_vm_device(vm_xml.VMXML.new_from_dumpxml(vm_name), iface_dev)
        test.log.debug("VM xml afater updating ifaces: %s.",
                       vm_xml.VMXML.new_from_dumpxml(vm_name))

    def run_test(dev_type):
        """
        Check the locked memory for the vm with multiple vDPA interfaces.

        1. The locked memory will be 1G + current memory * ${num of vDPA interface};
        2. Hot unplug the vDPA interface or Memory device will not decrease
            the locked memory;
        3. When hotplug a vDPA interface, the locked memory will not increase
            if there is enough locked memory(>=1G + current memory * ${num of vDPA interface});
        4. No error or fail in the qemu log.
        """
        test.log.info("TEST_STEP1: Start the vm with a vDPA interface.")
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

        qemu_log = "/var/log/libvirt/qemu/%s.log" % vm.name
        process.run("echo > {}".format(qemu_log), shell=True)
        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after VM startup!")

        test.log.info("TEST_STEP2: Hotplug memory device.")
        mem_dict1 = eval(params.get('mem_dict1', '{}'))
        mem_dev = libvirt_vmxml.create_vm_device_by_type("memory", mem_dict1)
        virsh.attach_device(vm_name, mem_dev.xml, **virsh_args)

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after attaching a memory "
                      "device!")

        test.log.info("TEST_STEP3: Add one more vDPA interface.")
        iface2 = interface_base.create_iface(dev_type, eval(params.get('iface_dict2', "{'source': {'dev':'/dev/vhost-vdpa-1'}}")))
        virsh.attach_device(vm_name, iface2.xml, **virsh_args)

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) * 2 + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after attaching the 2nd "
                      "interface device!")

        test.log.info("TEST_STEP4: Hotplut one more memory device.")
        mem_dict2 = eval(params.get('mem_dict2', '{}'))
        mem_dev2 = libvirt_vmxml.create_vm_device_by_type("memory", mem_dict2)
        virsh.attach_device(vm_name, mem_dev2.xml, **virsh_args)

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) * 2 + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after attaching the 2nd "
                      "interface device!")

        test.log.info("TEST_STEP5: Detach a memory device and vDPA interface.")
        memxml = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('memory')[-1]
        virsh.detach_device(vm_name, memxml.xml, debug=True)
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after detaching a memory device!")

        iface = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('interface')[-1]
        virsh.detach_device(vm_name, iface.xml,
                            wait_for_event=True,
                            **virsh_args)
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after detaching a vDPA interface!")

        test.log.info("TEST_STEP6: Hotplug a vDPA interface.")
        virsh.attach_device(vm_name, iface.xml, **virsh_args)
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after re-attaching a vDPA interface!")

        test.log.info("TEST_STEP7: Hotplug one more vDPA interface.")
        iface3 = interface_base.create_iface(dev_type, eval(params.get('iface_dict3', "{'source': {'dev':'/dev/vhost-vdpa-2'}}")))
        virsh.attach_device(vm_name, iface3.xml, **virsh_args)

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) * 3 + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after attaching the 3rd "
                      "interface device!")

        test.log.info("TEST_STEP8: Check qemu log.")
        libvirt.check_logfile("fail|error", qemu_log, str_in_log=False)

    libvirt_version.is_libvirt_feature_supported(params)
    # Variable assignment
    dev_type = params.get('dev_type', 'vdpa')
    virsh_args = {'debug': True, 'ignore_status': False}
    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    try:
        pf_pci = utils_vdpa.get_vdpa_pci()
        test_env_obj = utils_vdpa.VDPAOvsTest(pf_pci)
        test_env_obj.setup()
        setup_test(dev_type)
        run_test(dev_type)

    finally:
        backup_vmxml.sync()
        test_env_obj.cleanup()
