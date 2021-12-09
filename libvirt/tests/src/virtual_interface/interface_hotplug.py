import logging

from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_vdpa
from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.utils_libvirt import libvirt_vmxml

from provider.interface import interface_base
from provider.interface import vdpa_base


def run(test, params, env):
    """
    Test Hotplug/unplug interface device(s)
    """

    def setup_default():
        """
        Default setup
        """
        logging.debug("Remove VM's interface devices.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')

    def teardown_default():
        """
        Default cleanup
        """
        pass

    def setup_vdpa():
        """
        Setup vDPA environment
        """
        setup_default()
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
        if test_target != "simulator":
            service.Factory.create_service("NetworkManager").restart()
        if test_obj:
            test_obj.cleanup()

    def test_vdpa():
        """
        Hotplug/unplug vDPA type interface

        1) Start the vm, hotplug the interface
        2) Login to the vm and check the network function
        3) Hot-unplug the interface
        """
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)

        br_name = None
        if test_target == "mellanox":
            br_name = test_obj.br_name
        for _i in range(eval(params.get('repeat_times', '1'))):
            interface_base.attach_iface_device(vm_name, dev_type, params)
            vdpa_base.check_vdpa_conn(vm_session, test_target, br_name)
            interface_base.detach_iface_device(vm_name, dev_type)

    libvirt_version.is_libvirt_feature_supported(params)
    supported_qemu_ver = eval(params.get('func_supported_since_qemu_kvm_ver', '()'))
    if supported_qemu_ver:
        if not utils_misc.compare_qemu_version(*supported_qemu_ver, False):
            test.cancel("Current qemu version doesn't support this test!")

    # Variable assignment
    test_target = params.get('test_target', '')
    dev_type = params.get('dev_type', '')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    run_test = eval("test_%s" % dev_type)
    setup_test = eval("setup_%s" % dev_type) if "setup_%s" % dev_type in \
        locals() else setup_default
    teardown_test = eval("teardown_%s" % dev_type) if "teardown_%s" % \
        dev_type in locals() else teardown_default

    test_obj = None
    try:
        # Execute test
        test_obj = setup_test()
        run_test()

    finally:
        backup_vmxml.sync()
        teardown_test()
