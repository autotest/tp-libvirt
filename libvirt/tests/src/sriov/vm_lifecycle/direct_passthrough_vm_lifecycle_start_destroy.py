from provider.sriov import sriov_base
from provider.sriov import check_points

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_network
from virttest.utils_test import libvirt


def run(test, params, env):
    """
    start vm with direct type + passthrough mode interface
    """
    def run_test():
        """
        start vm with direct type + passthrough mode interface with vf
        """

        test.log.info("TEST_STEP1: Attach a direct interface to VM")
        iface_dev = sriov_test_obj.create_iface_dev(dev_type, iface_dict)
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        libvirt.add_vm_device(vmxml, iface_dev)

        test.log.info("TEST_STEP2: Start the VM")
        vm.start()
        vm_session = vm.wait_for_serial_login(timeout=240)

        test.log.info("TEST_STEP3: Check interface xml after VM startup")
        if dev_type == "network_interface":
            iface_dict.update({'type_name': 'direct'})
        check_points.comp_hostdev_xml(vm, device_type, iface_dict)

        test.log.info("TEST_STEP4: Check interface information - mac, "
                      "vlan, network accessibility and so on")
        check_points.check_mac_addr(
            vm_session, vm.name, device_type, iface_dict)

        if 'vlan' in iface_dict:
            check_points.check_vlan(sriov_test_obj.pf_name, iface_dict)
        else:
            check_points.check_vm_network_accessed(vm_session)

        if network_dict:
            libvirt_network.check_network_connection(
                network_dict.get("net_name"), 1)

        test.log.info("TEST_STEP5: Destroy VM")
        vm.destroy(gracefully=False)

        test.log.info("TEST_STEP6: Check environment recovery - mac, vlan ")
        if network_dict:
            libvirt_network.check_network_connection(
                network_dict.get("net_name"), 0)

        check_points.check_vlan(sriov_test_obj.pf_name, iface_dict, True)

        check_points.check_mac_addr_recovery(
            sriov_test_obj.pf_name, device_type, iface_dict)

    dev_type = params.get("dev_type", "")

    vm_name = params.get("main_vm", "avocado-vt-vm1")
    vm = env.get_vm(vm_name)
    sriov_test_obj = sriov_base.SRIOVTest(vm, test, params)

    iface_dict = sriov_test_obj.parse_iface_dict()
    network_dict = sriov_test_obj.parse_network_dict()
    device_type = params.get("device_type", "interface")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    orig_config_xml = vmxml.copy()

    try:
        sriov_test_obj.setup_default(network_dict=network_dict)
        run_test()

    finally:
        sriov_test_obj.teardown_default(orig_config_xml, network_dict=network_dict)
