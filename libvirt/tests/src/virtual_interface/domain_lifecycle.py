import logging
import os

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_misc
from virttest import utils_vdpa
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.staging import service
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import interface_base
from provider.interface import check_points

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test domain lifecycle
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
        Test domain lifecycle

        1) Start the vm and check network
        2) Destroy and start the VM, and check network
        3) Save and restore, and check network
        4) Suspend and resume, and check network
        5) Reboot the VM and check the network
        """
        # Setup Iface device
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_dict = eval(params.get('iface_dict', '{}'))
        iface_dev = interface_base.create_iface(dev_type, iface_dict)
        libvirt.add_vm_device(vmxml, iface_dev)

        logging.info("Start a VM with a '%s' type interface.", dev_type)
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()
        check_points.check_network_accessibility(vm, test_obj=test_obj, **params)

        logging.info("Destroy and start the VM.")
        virsh.destroy(vm.name, **VIRSH_ARGS)
        virsh.start(vm.name, **VIRSH_ARGS)
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, config_vdpa=True, **params)

        logging.info("Save the VM.")
        save_error = "yes" == params.get("save_error", "no")
        save_path = os.path.join(data_dir.get_tmp_dir(), vm.name + '.save')
        res = virsh.save(vm.name, 'sss', debug=True)
        libvirt.check_exit_status(res, expect_error=save_error)
        if not save_error:
            logging.info("Restore vm.")
            virsh.restore(save_path, **VIRSH_ARGS)
            check_points.check_network_accessibility(
                vm, test_obj=test_obj, config_vdpa=False, **params)

        logging.info("Suspend and resume the vm.")
        virsh.suspend(vm.name, **VIRSH_ARGS)
        if not libvirt.check_vm_state(vm_name, "paused"):
            test.fail("VM should be paused!")
        virsh.resume(vm.name, **VIRSH_ARGS)
        if not libvirt.check_vm_state(vm_name, "running"):
            test.fail("VM should be running!")
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, config_vdpa=False, **params)

        logging.debug("Reboot VM and check network.")
        virsh.reboot(vm.name, **VIRSH_ARGS)
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, config_vdpa=False, **params)

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
