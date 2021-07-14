import logging

from provider.sriov import sriov_base

from virttest import libvirt_version
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest import utils_net
from virttest import utils_sriov
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.libvirt_xml.devices import interface
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    Test when the PCI configuration file is in read-only mode
    """
    def test_vf_hotplug():
        """
        Hot-plug VF to VM

        """
        logging.info("Preparing a running guest...")
        libvirt_vmxml.remove_vm_devices_by_type(vm, 'interface')
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=180)

        logging.info("Attaching VF to the guest...")
        mac_addr = utils_net.generate_mac_address_simple()
        iface_dict = eval(params.get('iface_dict', '{"hostdev_addr": "%s"}')
                          % utils_sriov.pci_to_addr(vf_pci))
        iface = interface.Interface("hostdev")
        iface.xml = libvirt.modify_vm_iface(vm.name, "get_xml", iface_dict)
        virsh.attach_device(vm_name, iface.xml, debug=True, ignore_status=False)

        logging.info("Checking VF in the guest...")
        vm_iface_types = [iface.get_type_name() for iface in vm_xml.VMXML.
                          new_from_dumpxml(vm_name).devices.
                          by_device_tag("interface")]
        if 'hostdev' not in vm_iface_types:
            test.fail('Unable to get hostdev interface!')
        if cmd_in_vm:
            vm_session.cmd(cmd_in_vm)
        vm_session.close()

    libvirt_version.is_libvirt_feature_supported(params)

    test_case = params.get("test_case", "")
    run_test = eval("test_%s" % test_case)
    cmd_in_vm = params.get("cmd_in_vm")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    pf_pci = utils_sriov.get_pf_pci()
    if not pf_pci:
        test.cancel("NO available pf found.")
    default_vf = sriov_base.setup_vf(pf_pci, params)
    vf_pci = utils_sriov.get_vf_pci_id(pf_pci)
    dev_name = utils_sriov.get_device_name(vf_pci)

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()
    libvirtd = utils_libvirtd.Libvirtd('virtqemud')
    try:
        virsh.nodedev_detach(dev_name, debug=True, ignore_status=False)
        logging.info("Re-mounting sysfs with ro mode...")
        utils_misc.mount('/sys', '', None, 'remount,ro')
        libvirtd.restart()
        run_test()
    finally:
        logging.info("Recover test enviroment.")
        utils_misc.mount('/sys', '', None, 'remount,rw')
        sriov_base.recover_vf(pf_pci, params, default_vf)
        if vm.is_alive():
            vm.destroy(gracefully=False)
        orig_config_xml.sync()
        virsh.nodedev_reattach(dev_name, debug=True)
