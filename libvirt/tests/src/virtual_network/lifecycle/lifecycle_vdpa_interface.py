import os

from virttest import data_dir
from virttest import libvirt_version
from virttest import utils_misc
from virttest import virsh
from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt

from provider.interface import check_points
from provider.interface import interface_base
from provider.interface import vdpa_base

VIRSH_ARGS = {'debug': True, 'ignore_status': False}


def run(test, params, env):
    """
    Test domain lifecycle
    """
    def run_test(dev_type, params, test_obj=None):
        """
        Test domain lifecycle

        1) Start the vm and check network
        2) Destroy and start the VM, and check network
        3) Save and restore, and check network
        4) Suspend and resume, and check network
        5) Reboot the VM and check the network

        :param dev_type: Device type
        :param params: Dictionary with the test parameters
        :test_obj: Object of vDPA test
        """
        # Setup Iface device
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        iface_dict = interface_base.parse_iface_dict(params)
        iface_dev = interface_base.create_iface(dev_type, iface_dict)
        libvirt.add_vm_device(vmxml, iface_dev)
        iface_dict2 = eval(params.get("iface_dict2", "{}"))
        if iface_dict2:
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm_name),
                "interface", iface_dict2, 2)

        test.log.info("Start a VM with a '%s' type interface.", dev_type)
        vm.start()
        vm.wait_for_serial_login(timeout=240).close()
        check_points.check_network_accessibility(vm, test_obj=test_obj, **params)

        test.log.info("Destroy and start the VM.")
        virsh.destroy(vm.name, **VIRSH_ARGS)
        virsh.start(vm.name, **VIRSH_ARGS)
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, config_vdpa=True, **params)

        test.log.info("Save the VM.")
        save_error = "yes" == params.get("save_error", "no")
        save_path = os.path.join(data_dir.get_tmp_dir(), vm.name + '.save')
        res = virsh.save(vm.name, 'sss', debug=True)
        libvirt.check_exit_status(res, expect_error=save_error)
        if not save_error:
            test.log.info("Restore vm.")
            virsh.restore(save_path, **VIRSH_ARGS)
            check_points.check_network_accessibility(
                vm, test_obj=test_obj, config_vdpa=False, **params)

        test.log.info("Suspend and resume the vm.")
        virsh.suspend(vm.name, **VIRSH_ARGS)
        if not libvirt.check_vm_state(vm_name, "paused"):
            test.fail("VM should be paused!")
        virsh.resume(vm.name, **VIRSH_ARGS)
        if not libvirt.check_vm_state(vm_name, "running"):
            test.fail("VM should be running!")
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, config_vdpa=False, **params)

        test.log.debug("Reboot VM and check network.")
        virsh.reboot(vm.name, **VIRSH_ARGS)
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, config_vdpa=False, **params)

    libvirt_version.is_libvirt_feature_supported(params)
    utils_misc.is_qemu_function_supported(params)

    # Variable assignment
    test_target = params.get('test_target', '')
    dev_type = params.get('dev_type', 'vdpa')

    vm_name = params.get('main_vm')
    vm = env.get_vm(vm_name)

    vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
    backup_vmxml = vmxml.copy()

    test_obj = None
    try:
        # Execute test
        test_obj, test_dict = vdpa_base.setup_vdpa(vm, params)
        run_test(dev_type, test_dict, test_obj=test_obj)

    finally:
        backup_vmxml.sync()
        vdpa_base.cleanup_vdpa(test_target, test_obj)
