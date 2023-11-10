from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_vdpa
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.utils_libvirt import libvirt_memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Hotplug memory to vm with vDPA type interface and check the locked memory
    """

    def check_environment(params):
        """
        Check the test environment

        :param params: Dictionary with the test parameters
        """
        libvirt_version.is_libvirt_feature_supported(params)
        utils_misc.is_qemu_function_supported(params)

    def setup_test():
        """
        Set up test

        1) Remove interface devices
        2) Setup test environment for a specific interface if needed
        3) Set VM attrs

        :return: An object of special test environment
        """
        test.log.debug("Remove VM's interface devices.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        test_env_obj = None
        if test_target == "simulator":
            test_env_obj = utils_vdpa.VDPASimulatorTest()
        else:
            pf_pci = utils_vdpa.get_vdpa_pci()
            test_env_obj = utils_vdpa.VDPAOvsTest(pf_pci)
        test_env_obj.setup()

        test.log.debug("Update VM's settings.")
        vm_attrs = eval(params.get('vm_attrs', '{}'))
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        test.log.debug("Updated VM xml: %s.",
                       vm_xml.VMXML.new_from_dumpxml(vm_name))
        return test_env_obj

    def teardown_test():
        """
        Cleanup vDPA environment
        """
        if test_obj:
            test_obj.cleanup()

    def setup_at_memory_to_vm_with_iface():
        """
        Prepare a vm with max memory, numa, and an interface
        """
        test_env_obj = setup_test()
        # Add interface device
        iface_dict = eval(params.get('iface_dict', '{}'))
        iface_dev = interface_base.create_iface(dev_type, iface_dict)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt.add_vm_device(vmxml, iface_dev)
        test.log.debug("VM xml after updating ifaces: %s.",
                       vm_xml.VMXML.new_from_dumpxml(vm_name))
        return test_env_obj

    def test_at_memory_to_vm_with_iface():
        """
        hotplug memory device to vm with an interface

        1) Start vm and check the locked memory
        2) Hotplug memory device and check the locked memory
        """
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()
        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        # MEMLOCK value is guest memory + 1G(for the passthrough device)
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK after VM startup!")

        test.log.info("Hotplug memory device.")
        mem_dict = eval(params.get('mem_dict', '{}'))
        memxml = Memory()
        memxml.setup_attrs(**mem_dict)
        virsh.attach_device(vm_name, memxml.xml, **VIRSH_ARGS)
        expr_memlock += libvirt_memory.normalize_mem_size(
            mem_dict['target']['size'], mem_dict['target']['size_unit'])
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK after attaching a memory "
                      "device!")

    def test_at_iface_and_memory():
        """
        hotplug an interface and memory devices

        1) Start vm and check the default locked memory
        2) Hotplug an interface and check the locked memory
        3) Hotplug 2 memory devices and check the locked memory
        4) Hot-unplug a memory device and check the locked memory
        """
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()
        expr_memlock = 67108864
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct default!")

        interface_base.attach_iface_device(vm_name, dev_type, params)

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # MEMLOCK value is guest memory + 1G(for the passthrough device)
        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) + 1073741824
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK after VM startup!")

        test.log.info("Hotplug memory devices.")
        for mem_attrs in ['mem_dict1', 'mem_dict2']:
            mem_dict = eval(params.get(mem_attrs, '{}'))
            memxml = Memory()
            memxml.setup_attrs(**mem_dict)
            virsh.attach_device(vm_name, memxml.xml, **VIRSH_ARGS)
            expr_memlock += libvirt_memory.normalize_mem_size(
                mem_dict['target']['size'], mem_dict['target']['size_unit'])
            if not libvirt_memory.comp_memlock(expr_memlock):
                test.fail("Unable to get correct MEMLOCK after attaching a "
                          "memory device!")

        test.log.info("Detach a memory device and check memlock.")
        memxml = vm_xml.VMXML.new_from_dumpxml(
            vm_name).get_devices('memory')[-1]
        cmd_result = virsh.detach_device(vm_name, memxml.xml,
                                         wait_for_event=True,
                                         debug=True)
        if cmd_result.exit_status:
            libvirt.check_result(cmd_result, 'unplug of device was rejected')
            if not libvirt_memory.comp_memlock(expr_memlock):
                test.fail("Detaching mem failed, MEMLOCK should not change!")
        else:
            if not libvirt_memory.comp_memlock(expr_memlock):
                test.fail("Unable to get correct MEMLOCK after detaching a "
                          "memory device!")

    def setup_at_memory_to_vm_with_iface_and_locked_mem():
        """
        Prepare a vm with max memory, locked mem, numa, and an interface
        """
        return setup_at_memory_to_vm_with_iface()

    def test_at_memory_to_vm_with_iface_and_locked_mem():
        """
        hotplug memory device

        1) Start a guest with max memory + locked + interface
        2) Hotplug a memory device and check the locked memory
        """
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()
        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        expr_memlock = libvirt_memory.normalize_mem_size(
            new_vmxml.memtune.hard_limit, new_vmxml.memtune.hard_limit_unit)
        if not libvirt_memory.comp_memlock(expr_memlock):
            test.fail("Unable to get correct MEMLOCK after VM startup!")

    check_environment(params)
    # Variable assignment
    test_scenario = params.get('test_scenario', '')
    test_target = params.get('test_target', '')
    dev_type = params.get('dev_type', 'vdpa')
    run_test = eval("test_%s" % test_scenario)
    setup_func = eval("setup_%s" % test_scenario) if "setup_%s" % \
        test_scenario in locals() else setup_test

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    test_obj = None
    try:
        # Execute test
        test_obj = setup_func()
        run_test()

    finally:
        backup_vmxml.sync()
        teardown_test()
