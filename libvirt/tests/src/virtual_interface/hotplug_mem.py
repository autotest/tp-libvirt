import logging as log

from avocado.core import exceptions
from avocado.utils import process

from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_vdpa
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices.memory import Memory
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def check_soft_memlock(exp_memlock):
    """
    Check the soft locked mem

    :param exp_memlock: The expected locked mem value
    :raise: test.error if the actual locked mem is invalid
    :return: True on success
    """
    logging.debug("Check if the memlock is %s.", exp_memlock)
    cmd = "prlimit -p `pidof qemu-kvm` |awk '/MEMLOCK/ {print $6}'"
    tmp_act_memlock = process.run(cmd, shell=True).stdout_text.strip()
    logging.debug("Actual memlock is {}.".format(tmp_act_memlock))
    try:
        act_memlock = int(tmp_act_memlock)
    except ValueError as e:
        raise exceptions.TestError(e)
    return exp_memlock == act_memlock


def normalize_mem_size(mem_size, mem_unit):
    """
    Normalize the mem size and convert it to bytes

    :param mem_size: The mem size
    :param mem_unit: The mem size unit
    :return: Byte format size
    """
    return mem_size*1024**['B', 'K', 'M', 'G', 'T'].index(mem_unit[0].upper())


def run(test, params, env):
    """
    Hotplug memory with interface
    """

    def check_environment(params):
        """
        Check the test environment

        :param params: Dictionary with the test parameters
        """
        libvirt_version.is_libvirt_feature_supported(params)
        utils_misc.is_qemu_function_supported(params)

    def setup_test(dev_type):
        """
        Set up test

        1) Remove interface devices
        2) Setup test environment for a specific interface if needed
        3) Set VM attrs

        :param dev_type: interface type
        :return: An object of special test environment
        """
        logging.debug("Remove VM's interface devices.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        test_env_obj = None
        if dev_type == 'vdpa':
            test_env_obj = setup_vdpa()

        logging.debug("Update VM's settings.")
        vm_attrs = eval(params.get('vm_attrs', '{}'))
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vmxml.setup_attrs(**vm_attrs)
        vmxml.sync()
        logging.debug("Updated VM xml: %s.",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))
        return test_env_obj

    def teardown_test(dev_type):
        """
        Default cleanup

        :param dev_type: interface type
        """
        if dev_type == 'vdpa':
            teardown_vdpa()

    def setup_vdpa():
        """
        Setup vDPA environment
        """
        test_env_obj = None
        if test_target == "simulator":
            test_env_obj = utils_vdpa.VDPASimulatorTest()
        else:
            pf_pci = utils_vdpa.get_vdpa_pci()
            test_env_obj = utils_vdpa.VDPAOvsTest(pf_pci)
        test_env_obj.setup()
        return test_env_obj

    def teardown_vdpa():
        """
        Cleanup vDPA environment
        """
        if test_obj:
            test_obj.cleanup()

    def setup_at_memory_to_vm_with_iface(dev_type):
        """
        Prepare a vm with max memory, numa, and an interface

        :param dev_type: interface type
        """
        test_env_obj = setup_test(dev_type)
        # Add interface device
        iface_dict = eval(params.get('iface_dict', '{}'))
        iface_dev = interface_base.create_iface(dev_type, iface_dict)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt.add_vm_device(vmxml, iface_dev)
        logging.debug("VM xml afater updating ifaces: %s.",
                      vm_xml.VMXML.new_from_dumpxml(vm_name))
        return test_env_obj

    def test_at_memory_to_vm_with_iface(dev_type):
        """
        hotplug memory device to vm with an interface

        1) Start vm and check the locked memory
        2) Hotplug memory device and check the locked memory

        :param dev_type: interface type
        """
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()
        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        # MEMLOCK value is guest memory + 1G(for the passthrough device)
        expr_memlock = normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) + 1073741824
        if not check_soft_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after VM startup!")

        logging.info("Hotplug memory device.")
        mem_dict = eval(params.get('mem_dict', '{}'))
        memxml = Memory()
        memxml.setup_attrs(**mem_dict)
        virsh.attach_device(vm_name, memxml.xml, **VIRSH_ARGS)
        expr_memlock += normalize_mem_size(
            mem_dict['target']['size'], mem_dict['target']['size_unit'])
        if not check_soft_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after attaching a memory "
                      "device!")

    def test_at_iface_and_memory(dev_type):
        """
        hotplug an interface and memory devices

        1) Start vm and check the default locked memory
        2) Hotplug an interface and check the locked memory
        3) Hotplug 2 memory devices and check the locked memory
        4) Hot-unplug a memory device and check the locked memory

        :param dev_type: interface type
        """
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()
        expr_memlock = 67108864
        if not check_soft_memlock(expr_memlock):
            test.fail("Unalbe to get correct default!")

        interface_base.attach_iface_device(vm_name, dev_type, params)

        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        # MEMLOCK value is guest memory + 1G(for the passthrough device)
        expr_memlock = normalize_mem_size(
            new_vmxml.get_current_mem(),
            new_vmxml.get_current_mem_unit()) + 1073741824
        if not check_soft_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after VM startup!")

        logging.info("Hotplug memory devices.")
        for mem_attrs in ['mem_dict1', 'mem_dict2']:
            mem_dict = eval(params.get(mem_attrs, '{}'))
            memxml = Memory()
            memxml.setup_attrs(**mem_dict)
            virsh.attach_device(vm_name, memxml.xml, **VIRSH_ARGS)
            expr_memlock += normalize_mem_size(
                mem_dict['target']['size'], mem_dict['target']['size_unit'])
            if not check_soft_memlock(expr_memlock):
                test.fail("Unalbe to get correct MEMLOCK after attaching a "
                          "memory device!")

        logging.info("Detach a memory device and check memlock.")
        memxml = vm_xml.VMXML.new_from_dumpxml(vm_name).get_devices('memory')[-1]
        cmd_result = virsh.detach_device(vm_name, memxml.xml,
                                         wait_for_event=True,
                                         debug=True)
        if cmd_result.exit_status:
            libvirt.check_result(cmd_result, 'unplug of device was rejected')
            if not check_soft_memlock(expr_memlock):
                test.fail("Detaching mem failed, MEMLOCK should not change!")
        else:
            if not check_soft_memlock(expr_memlock):
                test.fail("Unalbe to get correct MEMLOCK after detaching a "
                          "memory device!")

    def setup_at_memory_to_vm_with_iface_and_locked_mem(dev_type):
        """
        Prepare a vm with max memory, locked mem, numa, and an interface

        :param dev_type: interface type
        """
        return setup_at_memory_to_vm_with_iface(dev_type)

    def test_at_memory_to_vm_with_iface_and_locked_mem(dev_type):
        """
        hotplug memory device

        1) Start a guest with max memory + locked + interface
        2) Hotplug a memory device and check the locked memory

        :param dev_type: interface type
        """
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()
        new_vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)

        expr_memlock = normalize_mem_size(
            new_vmxml.memtune.hard_limit, new_vmxml.memtune.hard_limit_unit)
        if not check_soft_memlock(expr_memlock):
            test.fail("Unalbe to get correct MEMLOCK after VM startup!")

    check_environment(params)
    # Variable assignment
    test_scenario = params.get('test_scenario', '')
    test_target = params.get('test_target', '')
    dev_type = params.get('dev_type', '')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    setup_func = eval("setup_%s" % test_scenario) if "setup_%s" % \
        test_scenario in locals() else setup_test
    run_test = eval("test_%s" % test_scenario)
    teardown_func = eval("teardown_%s" % test_scenario) if "teardown_%s" % \
        test_scenario in locals() else teardown_test

    test_obj = None
    try:
        # Execute test
        test_obj = setup_func(dev_type)
        run_test(dev_type)

    finally:
        backup_vmxml.sync()
        teardown_func(dev_type)
