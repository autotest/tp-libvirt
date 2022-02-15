import os
import signal

from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_sriov
from virttest import utils_vdpa
from virttest import virsh

from virttest.libvirt_xml import nodedev_xml
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_pcicontr
from virttest.utils_test import libvirt

from provider.interface import interface_base


VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def check_environment(params):
    """
    Check the test environment

    :param params: Dictionary with the test parameters
    """
    libvirt_version.is_libvirt_feature_supported(params)
    utils_misc.is_qemu_function_supported(params)


def get_vdpa_vf_pci(dev_name):
    """
    Get the VF's pci from the device name

    :param dev_name: Name of the device

    :returns: VF's pci
    """
    dev_xml = nodedev_xml.NodedevXML.new_from_dumpxml(dev_name)
    return os.path.basename(os.path.dirname(dev_xml.get_path()))


def run(test, params, env):
    """
    Start the vm or hotplug with duplicate vDPA device or VF device
    """

    def exec_test(vm, test_scenario, iface_type, iface_args,
                  iface_type2, iface_args2, params=params):
        """
        Execute test

        :param vm: VM object
        :param test_scenario: Test scenario
        :param iface_type: Interface type for the first device
        :param iface_args: Interface attrs for the first device
        :param iface_type2: Interface type for the second device
        :param iface_args2: Interface attrs for the second device
        :param params: Test parameters
        """
        status_error = "yes" == params.get("status_error", "no")
        error_msg = params.get("error_msg")

        opts = '--config'
        test.log.info("TEST_STEP1: Attach a %s device", iface_type)
        iface = interface_base.create_iface(iface_type, iface_args)
        virsh.attach_device(vm.name, iface.xml, flagstr=opts, **VIRSH_ARGS)

        iface2 = interface_base.create_iface(iface_type2, iface_args2)
        if test_scenario.startswith("hotplug"):
            opts = ''
            libvirt_pcicontr.reset_pci_num(vm.name)
            test.log.info("TEST_STEP2: Start VM")
            vm.start()
            vm.wait_for_serial_login(timeout=240).close()
            test.log.info("TEST_STEP3: Attach a %s device.", iface_type2)
            result = virsh.attach_device(vm.name, iface2.xml, debug=True)
        else:
            test.log.info("TEST_STEP2: Attach a %s device.", iface_type2)
            virsh.attach_device(vm.name, iface2.xml, flagstr=opts, **VIRSH_ARGS)
            test.log.info("TEST_STEP3: Start VM")
            result = virsh.start(vm.name, debug=True)
        libvirt.check_exit_status(result, status_error)
        if error_msg:
            libvirt.check_result(result, error_msg)

    def setup_vdpa(test_target):
        """
        Setup vDPA environment

        :param test_target: Test target, simulator or mellanox
        :return: An object of vDPA test environment setup
        """
        test_env_obj = None
        test.log.info("TEST_SETUP: Setup vDPA environment.")
        if test_target == "simulator":
            test_env_obj = utils_vdpa.VDPASimulatorTest()
        else:
            pf_pci = utils_vdpa.get_vdpa_pci()
            test_env_obj = utils_vdpa.VDPAOvsTest(pf_pci)
        test_env_obj.setup()
        return test_env_obj

    def teardown_vdpa(test_obj):
        """
        Cleanup vDPA environment

        :param test_obj: An object of vDPA test environment settings
        """
        test.log.info("TEST_TEARDOWN: Clean up vDPA environment.")
        if test_obj:
            test_obj.cleanup()

    def test_coldplug_2_vdpa():
        """
        Start the VM with 2 vDPA interfaces with same source
        """
        iface_args = eval(iface_dict)
        iface_args2 = eval(iface_dict2)
        exec_test(vm, test_scenario, iface_type, iface_args,
                  iface_type2, iface_args2)

    def test_coldplug_vdpa_vf():
        """
        Start the VM with a vDPA device and the VF
        """
        iface_args = eval(iface_dict)
        vf_pci = get_vdpa_vf_pci(dev_name)
        iface_args2 = eval(iface_dict2 % utils_sriov.pci_to_addr(vf_pci))
        exec_test(vm, test_scenario, iface_type, iface_args,
                  iface_type2, iface_args2)

    def test_hotplug_same_vdpa_to_vm_with_vdpa_dev():
        """
        Hotplug the vDPA that is already in use
        """
        iface_args = eval(iface_dict)
        iface_args2 = eval(iface_dict2)
        exec_test(vm, test_scenario, iface_type, iface_args,
                  iface_type2, iface_args2)

    def test_hotplug_vdpa_to_vm_with_hostdev_iface():
        """
        Hotplug the vDPA device to the vm when the VF is in use
        """
        vf_pci = get_vdpa_vf_pci(dev_name)
        iface_args = eval(iface_dict % utils_sriov.pci_to_addr(vf_pci))
        iface_args2 = eval(iface_dict2)
        exec_test(vm, test_scenario, iface_type, iface_args,
                  iface_type2, iface_args2)

    def test_hotplug_hostdev_iface_to_vm_with_vdpa_dev():
        """
        Hotplug the VF to the vm with the vDPA device
        """
        iface_args = eval(iface_dict)
        iface = interface_base.create_iface(iface_type, iface_args)

        test.log.info("TEST_STEP1: Start the VM with %s device", iface_type)
        virsh.attach_device(vm.name, iface.xml, flagstr='--config', **VIRSH_ARGS)
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()

        test.log.info("TEST_STEP2: Assign the VF to the VM")
        vf_pci = get_vdpa_vf_pci(dev_name)
        result = virsh.attach_interface(
            vm.name, option="%s %s --managed" % (iface_type2, vf_pci),
            debug=True, timeout=20)

        # FIXME: virsh hangs here, need a way to recover the environment
        if result.exit_status == -15:
            utils_misc.safe_kill(vm.get_pid(), signal.SIGKILL)
            test.fail("virsh may hang when attaching the vf while vdpa device "
                      "is in use!")

        libvirt.check_exit_status(result, status_error)
        if error_msg:
            test.log.debug("error_msg: %s", error_msg)
            libvirt.check_result(result, error_msg)

    check_environment(params)
    # Variable assignment
    test_scenario = params.get('test_scenario', '')
    test_target = params.get('test_target', '')
    dev_name = params.get('dev_name')
    iface_type = params.get('iface_type', "vdpa")
    iface_dict = params.get('iface_dict', '{}')
    iface_type2 = params.get('iface_type2', 'network')
    iface_dict2 = params.get('iface_dict2', '{}')

    status_error = "yes" == params.get("status_error", "no")
    error_msg = params.get("error_msg")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()
    run_test = eval("test_%s" % test_scenario)

    test_obj = None
    try:
        # Execute test
        test.log.info("TEST_CASE: %s", run_test.__doc__.lstrip().split('\n\n')[0])
        test_obj = setup_vdpa(test_target)
        run_test()

    finally:
        orig_config_xml.sync()
        teardown_vdpa(test_obj)
