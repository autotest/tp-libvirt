import logging as log

from virttest import libvirt_version
from virttest import virsh
from virttest import utils_misc
from virttest import utils_vdpa
from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.interface import check_points

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test network connectivity
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

    def run_test(dev_type, params, test_obj=None):
        """
        Test the connectivity of vm's interface

        1) Start the vm with a interface
        2) Check the network driver of VM's interface
        3) Check the network connectivity
        4) Destroy the VM
        """
        # Setup Iface device
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_dict = eval(params.get('iface_dict', '{}'))
        iface_dev = interface_base.create_iface(dev_type, iface_dict)
        libvirt.add_vm_device(vmxml, iface_dev)

        logging.info("Start a VM with a '%s' type interface.", dev_type)
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)
        vm_iface_info = interface_base.get_vm_iface_info(vm_session)
        if params.get('vm_iface_driver'):
            if vm_iface_info.get('driver') != params.get('vm_iface_driver'):
                test.fail("VM iface should be {}, but got {}."
                          .format(params.get('vm_iface_driver'),
                                  vm_iface_info.get('driver')))

        logging.info("Check the network connectivity")
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, **params)
        virsh.destroy(vm.name, **VIRSH_ARGS)

    libvirt_version.is_libvirt_feature_supported(params)
    utils_misc.is_qemu_function_supported(params)

    # Variable assignment
    test_target = params.get('test_target', '')
    dev_type = params.get('dev_type', '')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    setup_test = eval("setup_%s" % dev_type) if "setup_%s" % dev_type in \
        locals() else setup_default
    teardown_test = eval("teardown_%s" % dev_type) if "teardown_%s" % \
        dev_type in locals() else teardown_default

    test_obj = None
    try:
        # Execute test
        test_obj = setup_test()
        run_test(dev_type, params, test_obj=test_obj)

    finally:
        backup_vmxml.sync()
        teardown_test()
