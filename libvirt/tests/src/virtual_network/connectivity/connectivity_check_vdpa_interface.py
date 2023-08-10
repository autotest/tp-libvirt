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
    Test network connectivity
    """
    def run_test(dev_type, params, test_obj=None):
        """
        Test the connectivity of vm's interface

        1) Start the vm with a interface
        2) Check the network driver of VM's interface
        3) Check the network connectivity
        4) Destroy the VM

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
        vm_session = vm.wait_for_serial_login(timeout=240)
        vm_iface_info = interface_base.get_vm_iface_info(vm_session)
        if params.get('vm_iface_driver'):
            if vm_iface_info.get('driver') != params.get('vm_iface_driver'):
                test.fail("VM iface should be {}, but got {}."
                          .format(params.get('vm_iface_driver'),
                                  vm_iface_info.get('driver')))
        check_points.comp_interface_xml(vm_xml.VMXML.new_from_dumpxml(vm_name),
                                        iface_dict)
        if expr_multiplier:
            pci_id = vdpa_base.get_iface_pci_id(vm_session)
            act_multiplier = vdpa_base.get_multiplier(vm_session, pci_id)
            if expr_multiplier != act_multiplier:
                test.fail("The multiplier should be {}, but got {}."
                          .format(expr_multiplier, act_multiplier))

        test.log.info("Check the network connectivity")
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, **params)
        virsh.destroy(vm.name, **VIRSH_ARGS)

    libvirt_version.is_libvirt_feature_supported(params)
    utils_misc.is_qemu_function_supported(params)

    # Variable assignment
    test_target = params.get('test_target', '')
    dev_type = params.get('dev_type', 'vdpa')
    expr_multiplier = params.get("expr_multiplier")
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
