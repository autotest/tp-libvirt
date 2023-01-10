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
    def update_vm_disk_boot(vm_name, disk_boot):
        """
        Update boot order of vm's 1st disk before test

        :param vm_name: vm name
        :param disk_boot: boot order of disk
        """
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        vm_os = vmxml.os
        vm_os.del_boots()
        vmxml.os = vm_os
        disk = vmxml.get_devices('disk')[0]
        target_dev = disk.target.get('dev', '')
        logging.debug('Set boot order %s to device %s', disk_boot, target_dev)
        vmxml.set_boot_order_by_target_dev(target_dev, disk_boot)
        vmxml.sync()

    def get_iface_pci_id(vm_session):
        """
        Get pci id of VM's interface

        :param vm_session: VM session
        :return: pci id of VM's interface
        """
        cmd = "lspci | awk '/Eth/ {print $1}'"
        return vm_session.cmd_output(cmd).splitlines()[0]

    def get_multiplier(vm_session, pci_id):
        """
        Get multiplier of VM's interface

        :param vm_session: VM session
        :param pci_id: The pci id
        :return: The multiplier of VM's interface
        """
        cmd = "lspci -vvv -s %s | awk -F '=' '/multiplier=/ {print $NF}'" % pci_id
        act_mul = vm_session.cmd_output(cmd).strip()
        logging.debug("Actual multiplier: %s", act_mul)
        return act_mul

    def setup_default():
        """
        Default setup
        """
        logging.debug("Remove VM's interface devices.")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        vm_attrs = eval(params.get('vm_attrs', '{}'))
        if vm_attrs:
            vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
            vmxml.setup_attrs(**vm_attrs)
            vmxml.sync()
        disk_boot = params.get('disk_boot')
        if disk_boot:
            update_vm_disk_boot(vm_name, disk_boot)

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
            test_env_obj.setup()
        else:
            vdpa_mgmt_tool_extra = params.get("vdpa_mgmt_tool_extra", "")
            pf_pci = utils_vdpa.get_vdpa_pci()
            test_env_obj = utils_vdpa.VDPAOvsTest(pf_pci, mgmt_tool_extra=vdpa_mgmt_tool_extra)
            test_env_obj.setup()
            params['mac_addr'] = test_env_obj.vdpa_mac.get(params.get("vdpa_dev", "vdpa0"))

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
        iface_dict = interface_base.parse_iface_dict(params)
        iface_dev = interface_base.create_iface(dev_type, iface_dict)
        libvirt.add_vm_device(vmxml, iface_dev)
        iface_dict2 = eval(params.get("iface_dict2", "{}"))
        if iface_dict2:
            libvirt_vmxml.modify_vm_device(
                vm_xml.VMXML.new_from_dumpxml(vm_name),
                "interface", iface_dict2, 2)

        logging.info("Start a VM with a '%s' type interface.", dev_type)
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
            pci_id = get_iface_pci_id(vm_session)
            act_multiplier = get_multiplier(vm_session, pci_id)
            if expr_multiplier != act_multiplier:
                test.fail("The multiplier should be {}, but got {}."
                          .format(expr_multiplier, act_multiplier))

        logging.info("Check the network connectivity")
        check_points.check_network_accessibility(
            vm, test_obj=test_obj, **params)
        virsh.destroy(vm.name, **VIRSH_ARGS)

    libvirt_version.is_libvirt_feature_supported(params)
    utils_misc.is_qemu_function_supported(params)

    # Variable assignment
    test_target = params.get('test_target', '')
    dev_type = params.get('dev_type', '')
    expr_multiplier = params.get("expr_multiplier")
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
